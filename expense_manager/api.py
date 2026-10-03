from __future__ import annotations

import asyncio
import calendar
import csv
import io
import json
import logging
import secrets
import socket
import statistics
import tempfile
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import categorizer, config, db, gmail_sync, ingest, llm, parser, sms_backup, tailscale

log = logging.getLogger("expense_manager")
STATIC = config.BASE_DIR / "expense_manager" / "static"
TOKEN = config.get_token()
_sync_lock = threading.Lock()


def run_gmail_sync(days: int | None = None) -> dict:
    if not _sync_lock.acquire(blocking=False):
        return {"ok": False, "error": "A Gmail sync is already running"}
    try:
        return gmail_sync.sync(days)
    finally:
        _sync_lock.release()


async def _gmail_poller():
    await asyncio.sleep(5)
    while True:
        if gmail_sync.configured():
            try:
                await asyncio.to_thread(run_gmail_sync)
            except Exception:
                log.exception("Gmail poll failed")
        await asyncio.sleep(max(1, config.GMAIL_POLL_MINUTES) * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    task = asyncio.create_task(_gmail_poller())
    yield
    task.cancel()


app = FastAPI(title="Expense Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _is_local(request: Request) -> bool:
    return (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost", "testclient")


def _is_trusted(request: Request) -> bool:
    """Local browser on the Mac, or your own tailnet identity arriving via `tailscale serve`."""
    if not _is_local(request):
        return False
    login = request.headers.get("tailscale-user-login")
    if login is None:
        return True
    return login.lower() in tailscale.owner_logins()


@app.middleware("http")
async def auth(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        token = request.headers.get("x-token") or request.query_params.get("token") or ""
        needs = path.startswith("/api/ingest/sms") or not _is_trusted(request)
        if needs and not secrets.compare_digest(token, TOKEN):
            return JSONResponse({"detail": "invalid or missing token"}, status_code=401)
    response = await call_next(request)
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"  # revalidate so UI updates show up without a hard refresh
    return response


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# --------------------------------------------------------------------------- ingestion

def _parse_ts(v) -> datetime | None:
    if v in (None, ""):
        return None
    try:
        n = float(v)
        if n > 1e12:
            n /= 1000
        return datetime.fromtimestamp(n)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return None


def _first(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


@app.post("/api/ingest/sms")
async def ingest_sms(request: Request):
    """Webhook for Android SMS forwarder apps. Accepts JSON, form-encoded or raw text."""
    raw = await request.body()
    data: dict = {}
    try:
        data = json.loads(raw, strict=False) if raw else {}  # strict=False: tolerate raw newlines in SMS text
        if not isinstance(data, dict):
            data = {}
    except ValueError:
        qs = parse_qs(raw.decode(errors="replace"))
        data = {k: v[0] for k, v in qs.items()} if qs and all(k.isidentifier() for k in qs) else {}
    if not data and raw:
        data = {"text": raw.decode(errors="replace")}
    data.update({k: v for k, v in request.query_params.items() if k != "token"})

    body = _first(data, "text", "message", "body", "msg", "content", "sms")
    if not body:
        # Still proves the phone can reach us; record it so Settings can show "forwarder last seen".
        with db.connect() as conn:
            db.set_meta(conn, "sms_last_heartbeat", ingest.fmt(datetime.now()))
            db.set_meta(conn, "sms_last_heartbeat_payload", raw[:1000].decode(errors="replace"))
        log.warning("SMS webhook payload without text: %r | content-type=%s",
                    raw[:600], request.headers.get("content-type"))
        # Reject (don't 200) so the app keeps the SMS queued instead of silently dropping it.
        raise HTTPException(400, "empty or unrecognised payload: set the forwarder's JSON template to "
                                 '{"from":"%from%","text":"%text%","receivedStamp":%receivedStamp%}')
    sender = str(_first(data, "from", "sender", "address", "phone", "number", "originatingAddress") or "")
    received = _parse_ts(_first(data, "receivedStamp", "sentStamp", "timestamp", "time", "date", "received_at"))
    ext = _first(data, "id", "messageId", "message_id")
    with db.connect() as conn:
        res = ingest.ingest_message(conn, source="sms", body=str(body), sender=sender, received_at=received,
                                    external_id=f"sms:{ext}" if ext else None)
    log.info("SMS from %s -> %s", sender, res.get("status"))
    return res


class TextIn(BaseModel):
    text: str
    sender: str = ""
    subject: str = ""
    source: str = "manual"
    received_at: str | None = None


@app.post("/api/ingest/text")
def ingest_text(p: TextIn):
    if p.source not in ("manual", "sms", "email"):
        raise HTTPException(400, "bad source")
    with db.connect() as conn:
        return ingest.ingest_message(conn, source=p.source, body=p.text, sender=p.sender, subject=p.subject,
                                     received_at=_parse_ts(p.received_at))


@app.post("/api/import/sms-backup")
async def import_sms_backup(request: Request):
    """Upload an SMS Backup & Restore XML as the raw request body."""
    with tempfile.NamedTemporaryFile(suffix=".xml") as tmp:
        size = 0
        async for chunk in request.stream():
            tmp.write(chunk)
            size += len(chunk)
        if not size:
            raise HTTPException(400, "empty upload")
        tmp.flush()
        res = await asyncio.to_thread(sms_backup.import_xml, tmp.name)
    log.info("SMS backup import: %s", res)
    return res


class SyncIn(BaseModel):
    days: int | None = None


@app.post("/api/sync/gmail")
async def sync_gmail(p: SyncIn | None = None):
    return await asyncio.to_thread(run_gmail_sync, p.days if p else None)


# --------------------------------------------------------------------------- queries

def _month_bounds(month: str | None) -> tuple[str, str, date, date]:
    today = date.today()
    if month:
        y, m = map(int, month.split("-"))
    else:
        y, m = today.year, today.month
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    return f"{start} 00:00:00", f"{end} 00:00:00", start, end


NS = tuple(sorted(categorizer.NON_SPEND))
NS_SQL = "(" + ",".join("?" * len(NS)) + ")"
SPEND_EXPR = "CASE WHEN direction='debit' THEN amount ELSE -amount END"
SPEND_WHERE = f"excluded=0 AND category NOT IN {NS_SQL}"


def _spend_between(conn, a: str, b: str) -> float:
    r = conn.execute(f"SELECT COALESCE(SUM({SPEND_EXPR}),0) s FROM transactions "
                     f"WHERE txn_time>=? AND txn_time<? AND {SPEND_WHERE}", (a, b, *NS)).fetchone()
    return round(r["s"], 2)


@app.get("/api/summary")
def summary(month: str | None = None):
    a, b, start, end = _month_bounds(month)
    today = date.today()
    days_in_month = (end - start).days
    is_current = start <= today < end
    days_elapsed = today.day if is_current else days_in_month

    with db.connect() as conn:
        total = _spend_between(conn, a, b)
        agg = conn.execute(
            """SELECT
                 COALESCE(SUM(CASE WHEN direction='credit' AND category='Income' THEN amount END),0) income,
                 COALESCE(SUM(CASE WHEN direction='debit' AND category='Investments' THEN amount END),0) invested,
                 COALESCE(SUM(CASE WHEN direction='debit' AND category='Credit Card Payment' THEN amount END),0) cc_paid,
                 COUNT(*) n
               FROM transactions WHERE txn_time>=? AND txn_time<? AND excluded=0""", (a, b)).fetchone()
        spend_count = conn.execute(
            f"SELECT COUNT(*) c FROM transactions WHERE txn_time>=? AND txn_time<? AND direction='debit' AND {SPEND_WHERE}",
            (a, b, *NS)).fetchone()["c"]

        pstart = (start - timedelta(days=1)).replace(day=1)
        prev_total = _spend_between(conn, f"{pstart} 00:00:00", a)
        pdays = (start - pstart).days
        same_day = pstart + timedelta(days=min(days_elapsed, pdays))
        prev_same_period = _spend_between(conn, f"{pstart} 00:00:00", f"{same_day} 00:00:00")

        budgets = {r["category"]: r["monthly_limit"] for r in conn.execute("SELECT * FROM budgets")}
        by_cat = [dict(r) for r in conn.execute(
            f"""SELECT category, ROUND(SUM({SPEND_EXPR}),2) amount, SUM(direction='debit') count
                FROM transactions WHERE txn_time>=? AND txn_time<? AND {SPEND_WHERE}
                GROUP BY category ORDER BY amount DESC""", (a, b, *NS))]
        for c in by_cat:
            c["budget"] = budgets.get(c["category"])
        for cat, lim in budgets.items():
            if not any(c["category"] == cat for c in by_cat):
                by_cat.append({"category": cat, "amount": 0, "count": 0, "budget": lim})

        daily_rows = {r["d"]: r for r in conn.execute(
            f"""SELECT date(txn_time) d, ROUND(SUM({SPEND_EXPR}),2) amount, SUM(direction='debit') count
                FROM transactions WHERE txn_time>=? AND txn_time<? AND {SPEND_WHERE} GROUP BY d""", (a, b, *NS))}
        daily, cum = [], 0.0
        for i in range(days_in_month):
            d = str(start + timedelta(days=i))
            r = daily_rows.get(d)
            amt = r["amount"] if r else 0
            cum += amt
            daily.append({"date": d, "amount": amt, "count": r["count"] if r else 0, "cumulative": round(cum, 2)})

        top_merchants = [dict(r) for r in conn.execute(
            f"""SELECT COALESCE(merchant, payee_vpa, 'Unknown') merchant, ROUND(SUM({SPEND_EXPR}),2) amount,
                       COUNT(*) count, MAX(category) category
                FROM transactions WHERE txn_time>=? AND txn_time<? AND {SPEND_WHERE}
                GROUP BY lower(COALESCE(merchant, payee_vpa, 'Unknown')) ORDER BY amount DESC LIMIT 10""",
            (a, b, *NS))]
        by_instrument = [dict(r) for r in conn.execute(
            f"""SELECT COALESCE(instrument,'OTHER') instrument, ROUND(SUM({SPEND_EXPR}),2) amount, COUNT(*) count
                FROM transactions WHERE txn_time>=? AND txn_time<? AND {SPEND_WHERE}
                GROUP BY instrument ORDER BY amount DESC""", (a, b, *NS))]
        by_account = [dict(r) for r in conn.execute(
            f"""SELECT COALESCE(bank,'?') || COALESCE(' ••' || account_last4, '') account,
                       ROUND(SUM({SPEND_EXPR}),2) amount, COUNT(*) count
                FROM transactions WHERE txn_time>=? AND txn_time<? AND {SPEND_WHERE}
                GROUP BY account ORDER BY amount DESC""", (a, b, *NS))]

    avg = round(total / days_elapsed, 2) if days_elapsed else 0
    return {
        "month": f"{start:%Y-%m}", "is_current": is_current, "days_elapsed": days_elapsed,
        "days_in_month": days_in_month, "total_spend": total, "income": round(agg["income"], 2),
        "invested": round(agg["invested"], 2), "cc_bill_paid": round(agg["cc_paid"], 2),
        "txn_count": spend_count, "avg_per_day": avg,
        "projected": round(avg * days_in_month, 2) if is_current else total,
        "prev_month_total": prev_total, "prev_same_period": prev_same_period,
        "change_pct": round((total - prev_same_period) / prev_same_period * 100, 1) if prev_same_period else None,
        "by_category": by_cat, "daily": daily, "top_merchants": top_merchants,
        "by_instrument": by_instrument, "by_account": by_account,
    }


@app.get("/api/trend")
def trend(months: int = 12):
    today = date.today().replace(day=1)
    first = today
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    with db.connect() as conn:
        rows = conn.execute(
            f"""SELECT strftime('%Y-%m', txn_time) m, category,
                       SUM(CASE WHEN category NOT IN {NS_SQL} THEN {SPEND_EXPR} ELSE 0 END) spend,
                       SUM(CASE WHEN category='Income' AND direction='credit' THEN amount ELSE 0 END) income,
                       SUM(CASE WHEN category='Investments' AND direction='debit' THEN amount ELSE 0 END) invested
                FROM transactions WHERE txn_time>=? AND excluded=0 GROUP BY m, category""",
            (*NS, f"{first} 00:00:00")).fetchall()
    out: dict[str, dict] = {}
    d = first
    while d <= today:
        out[f"{d:%Y-%m}"] = {"month": f"{d:%Y-%m}", "spend": 0.0, "income": 0.0, "invested": 0.0, "by_category": {}}
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    for r in rows:
        o = out.get(r["m"])
        if not o:
            continue
        o["spend"] += r["spend"]
        o["income"] += r["income"]
        o["invested"] += r["invested"]
        if r["category"] not in categorizer.NON_SPEND and r["spend"]:
            o["by_category"][r["category"]] = round(r["spend"], 2)
    for o in out.values():
        for k in ("spend", "income", "invested"):
            o[k] = round(o[k], 2)
    return list(out.values())


@app.get("/api/insights")
def insights(month: str | None = None):
    a, b, start, _ = _month_bounds(month)
    six_back = start
    for _ in range(6):
        six_back = (six_back - timedelta(days=1)).replace(day=1)
    with db.connect() as conn:
        hist = conn.execute(
            f"""SELECT lower(COALESCE(merchant, payee_vpa)) k, COALESCE(merchant, payee_vpa) name,
                       strftime('%Y-%m', txn_time) m, SUM(amount) amt, MAX(category) category
                FROM transactions WHERE direction='debit' AND txn_time>=? AND txn_time<? AND {SPEND_WHERE}
                  AND COALESCE(merchant, payee_vpa) IS NOT NULL
                GROUP BY k, m""", (f"{six_back} 00:00:00", b, *NS)).fetchall()
        weekday = [dict(r) for r in conn.execute(
            f"""SELECT CAST(strftime('%w', txn_time) AS INT) dow, ROUND(SUM({SPEND_EXPR}),2) amount
                FROM transactions WHERE txn_time>=? AND txn_time<? AND {SPEND_WHERE} GROUP BY dow""",
            (f"{six_back} 00:00:00", b, *NS))]
        biggest = [dict(r) for r in conn.execute(
            f"""SELECT id, txn_time, amount, merchant, category FROM transactions
                WHERE txn_time>=? AND txn_time<? AND direction='debit' AND {SPEND_WHERE}
                ORDER BY amount DESC LIMIT 5""", (a, b, *NS))]

    per: dict[str, dict] = defaultdict(lambda: {"months": {}, "name": None, "category": None})
    for r in hist:
        p = per[r["k"]]
        p["months"][r["m"]] = r["amt"]
        p["name"], p["category"] = r["name"], r["category"]
    recurring = []
    for k, p in per.items():
        vals = list(p["months"].values())
        if len(vals) >= 3:
            med = statistics.median(vals)
            if med > 0 and all(abs(v - med) / med <= 0.2 for v in vals):
                recurring.append({"merchant": p["name"], "category": p["category"], "monthly": round(med, 2),
                                  "months_seen": len(vals), "last_month": max(p["months"])})
    recurring.sort(key=lambda x: -x["monthly"])
    names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    wd = {r["dow"]: r["amount"] for r in weekday}
    return {
        "recurring": recurring[:15],
        "recurring_total": round(sum(r["monthly"] for r in recurring), 2),
        "weekday": [{"day": names[i], "amount": wd.get(i, 0)} for i in range(7)],
        "biggest": biggest,
    }


def _txn_filters(month, date_from, date_to, category, q, direction, instrument):
    where, args = ["1=1"], []
    if month:
        a, b, _, _ = _month_bounds(month)
        where.append("txn_time>=? AND txn_time<?")
        args += [a, b]
    if date_from:
        where.append("txn_time>=?")
        args.append(f"{date_from} 00:00:00")
    if date_to:
        where.append("txn_time<?")
        args.append(f"{date.fromisoformat(date_to) + timedelta(days=1)} 00:00:00")
    if category:
        where.append("category=?")
        args.append(category)
    if direction:
        where.append("direction=?")
        args.append(direction)
    if instrument:
        where.append("instrument=?")
        args.append(instrument)
    if q:
        where.append("(merchant LIKE ? OR payee_vpa LIKE ? OR notes LIKE ? OR ref_no LIKE ? OR bank LIKE ?)")
        args += [f"%{q}%"] * 5
    return " AND ".join(where), args


@app.get("/api/transactions")
def list_transactions(month: str | None = None, date_from: str | None = None, date_to: str | None = None,
                      category: str | None = None, q: str | None = None, direction: str | None = None,
                      instrument: str | None = None, limit: int = 1000, offset: int = 0):
    where, args = _txn_filters(month, date_from, date_to, category, q, direction, instrument)
    with db.connect() as conn:
        items = [dict(r) for r in conn.execute(
            f"SELECT * FROM transactions WHERE {where} ORDER BY txn_time DESC, id DESC LIMIT ? OFFSET ?",
            (*args, limit, offset))]
        agg = conn.execute(
            f"""SELECT COUNT(*) n,
                  COALESCE(SUM(CASE WHEN direction='debit' THEN amount END),0) debit,
                  COALESCE(SUM(CASE WHEN direction='credit' THEN amount END),0) credit
                FROM transactions WHERE {where}""", args).fetchone()
    return {"items": items, "total": agg["n"], "sum_debit": round(agg["debit"], 2),
            "sum_credit": round(agg["credit"], 2), "non_spend_categories": sorted(categorizer.NON_SPEND)}


@app.get("/api/transactions/{tid}")
def get_transaction(tid: int):
    with db.connect() as conn:
        t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
        if not t:
            raise HTTPException(404)
        raws = [dict(r) for r in conn.execute(
            "SELECT id, source, sender, subject, body, received_at, status FROM raw_messages WHERE transaction_id=?",
            (tid,))]
        for r in raws:
            r["text"] = parser.normalize(r["body"])  # readable text instead of email HTML
    return {**dict(t), "raw_messages": raws}


class TxnPatch(BaseModel):
    category: str | None = None
    merchant: str | None = None
    notes: str | None = None
    excluded: bool | None = None
    amount: float | None = None
    txn_time: str | None = None
    apply_to_similar: bool = False


@app.patch("/api/transactions/{tid}")
def patch_transaction(tid: int, p: TxnPatch):
    with db.connect() as conn:
        t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
        if not t:
            raise HTTPException(404)
        sets, args = [], []
        if p.merchant is not None:
            sets.append("merchant=?"); args.append(p.merchant.strip() or None)
        if p.notes is not None:
            sets.append("notes=?"); args.append(p.notes)
        if p.excluded is not None:
            sets.append("excluded=?"); args.append(int(p.excluded))
        if p.amount is not None:
            sets.append("amount=?"); args.append(round(p.amount, 2))
        if p.txn_time:
            when = _parse_ts(p.txn_time)
            if not when:
                raise HTTPException(400, "bad txn_time")
            sets.append("txn_time=?"); args.append(ingest.fmt(when))
        if p.category:
            if p.category not in categorizer.CATEGORIES:
                raise HTTPException(400, "unknown category")
            sets += ["category=?", "category_source='user'"]; args.append(p.category)
        if sets:
            conn.execute(f"UPDATE transactions SET {', '.join(sets)}, updated_at=datetime('now','localtime') "
                         f"WHERE id=?", (*args, tid))
        updated_similar = 0
        if p.category and p.apply_to_similar:
            key = (p.merchant or t["merchant"] or t["payee_vpa"] or "").strip()
            if key:
                updated_similar = ingest.add_rule(conn, key, p.category)
        row = dict(conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone())
    return {**row, "updated_similar": updated_similar}


class TxnIn(BaseModel):
    amount: float
    direction: str = "debit"
    merchant: str | None = None
    category: str | None = None
    txn_time: str | None = None
    instrument: str = "OTHER"
    notes: str | None = None


@app.post("/api/transactions")
def add_transaction(p: TxnIn):
    if p.direction not in ("debit", "credit") or p.amount <= 0:
        raise HTTPException(400, "bad amount/direction")
    when = _parse_ts(p.txn_time) or datetime.now()
    with db.connect() as conn:
        if p.category:
            cat, src = p.category, "user"
        else:
            cat, src = categorizer.categorize(conn, merchant=p.merchant, vpa=None, direction=p.direction,
                                              hint=None, use_llm=False)
        cur = conn.execute(
            """INSERT INTO transactions(txn_time, amount, direction, merchant, category, category_source,
                   instrument, source, sources, parsed_by, notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (ingest.fmt(when), round(p.amount, 2), p.direction, p.merchant, cat, src, p.instrument,
             "manual", "manual", "manual", p.notes))
        return dict(conn.execute("SELECT * FROM transactions WHERE id=?", (cur.lastrowid,)).fetchone())


@app.delete("/api/transactions/{tid}")
def delete_transaction(tid: int):
    with db.connect() as conn:
        conn.execute("UPDATE raw_messages SET status='ignored', note='transaction deleted by user', "
                     "transaction_id=NULL WHERE transaction_id=?", (tid,))
        conn.execute("DELETE FROM transactions WHERE id=?", (tid,))
    return {"ok": True}


@app.get("/api/export.csv")
def export_csv(month: str | None = None, date_from: str | None = None, date_to: str | None = None,
               category: str | None = None, q: str | None = None):
    where, args = _txn_filters(month, date_from, date_to, category, q, None, None)
    cols = ["txn_time", "amount", "direction", "merchant", "category", "bank", "account_last4", "instrument",
            "payee_vpa", "ref_no", "sources", "excluded", "notes"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    with db.connect() as conn:
        for r in conn.execute(f"SELECT {', '.join(cols)} FROM transactions WHERE {where} ORDER BY txn_time", args):
            w.writerow([r[c] for c in cols])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="expenses-{month or "all"}.csv"'})


# --------------------------------------------------------------------------- review queue

@app.get("/api/raw")
def list_raw(status: str = "unparsed", limit: int = 200):
    with db.connect() as conn:
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM raw_messages WHERE status=? ORDER BY received_at DESC LIMIT ?", (status, limit))]
        for m in items:
            m["text"] = parser.normalize(m["body"])
        counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM raw_messages GROUP BY status")}
    return {"items": items, "counts": counts}


class ReprocessIn(BaseModel):
    use_llm: bool = True


@app.post("/api/raw/{rid}/reprocess")
def reprocess_raw(rid: int, p: ReprocessIn | None = None):
    use_llm = p.use_llm if p else True
    with db.connect() as conn:
        r = conn.execute("SELECT status FROM raw_messages WHERE id=?", (rid,)).fetchone()
        if not r:
            raise HTTPException(404)
        if r["status"] in ("parsed", "duplicate"):
            raise HTTPException(400, "already converted to a transaction")
        return ingest.process_raw(conn, rid, use_llm=use_llm, force_llm=use_llm and llm.enabled())


@app.post("/api/raw/{rid}/dismiss")
def dismiss_raw(rid: int):
    with db.connect() as conn:
        conn.execute("UPDATE raw_messages SET status='ignored', note='dismissed by user' WHERE id=?", (rid,))
    return {"ok": True}


# --------------------------------------------------------------------------- settings

@app.get("/api/categories")
def categories():
    return {"categories": categorizer.CATEGORIES, "non_spend": sorted(categorizer.NON_SPEND)}


@app.get("/api/budgets")
def get_budgets():
    with db.connect() as conn:
        return {r["category"]: r["monthly_limit"] for r in conn.execute("SELECT * FROM budgets")}


@app.put("/api/budgets")
def put_budgets(budgets: dict[str, float | None]):
    with db.connect() as conn:
        for cat, lim in budgets.items():
            if cat not in categorizer.CATEGORIES:
                raise HTTPException(400, f"unknown category {cat}")
            if not lim:
                conn.execute("DELETE FROM budgets WHERE category=?", (cat,))
            else:
                conn.execute("INSERT INTO budgets(category, monthly_limit) VALUES(?,?) "
                             "ON CONFLICT(category) DO UPDATE SET monthly_limit=excluded.monthly_limit", (cat, lim))
        return {r["category"]: r["monthly_limit"] for r in conn.execute("SELECT * FROM budgets")}


@app.get("/api/rules")
def get_rules():
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM category_rules ORDER BY pattern")]


class RuleIn(BaseModel):
    pattern: str
    category: str


@app.post("/api/rules")
def add_rule(p: RuleIn):
    if p.category not in categorizer.CATEGORIES:
        raise HTTPException(400, "unknown category")
    with db.connect() as conn:
        return {"updated": ingest.add_rule(conn, p.pattern, p.category)}


@app.delete("/api/rules/{rid}")
def delete_rule(rid: int):
    with db.connect() as conn:
        conn.execute("DELETE FROM category_rules WHERE id=?", (rid,))
    return {"ok": True}


@app.post("/api/recategorize")
async def recategorize(only_other: bool = True):
    def run():
        with db.connect() as conn:
            return ingest.recategorize(conn, only_fallback=only_other)
    return {"updated": await asyncio.to_thread(run)}


def _lan_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


@app.get("/api/status")
def status(request: Request):
    with db.connect() as conn:
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM raw_messages GROUP BY status")}
        n_txn = conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
        last_sync = db.get_meta(conn, "gmail_last_sync")
        last_sms = conn.execute("SELECT MAX(received_at) m FROM raw_messages WHERE source='sms'").fetchone()["m"]
        last_hb = db.get_meta(conn, "sms_last_heartbeat")
    local = _is_trusted(request)
    tok = TOKEN if local else "<token>"
    ts = tailscale.ip()
    bases = ([tailscale.serve_url()] if tailscale.serve_url() else []) + [
        f"http://{h}:{config.PORT}" for h in ([ts] if ts else []) + [h for h in _lan_ips() if h != ts]]
    bases = bases or [f"http://<mac-ip>:{config.PORT}"]
    return {
        "gmail": {"configured": gmail_sync.configured(), "user": config.GMAIL_USER, "last_sync": last_sync,
                  "poll_minutes": config.GMAIL_POLL_MINUTES, "senders": config.GMAIL_SENDER_FILTERS},
        "llm": {"enabled": llm.enabled(), "model": config.LLM_MODEL, "base_url": config.LLM_BASE_URL},
        "sms": {"last_received": last_sms, "last_heartbeat": last_hb,
                "webhook_urls": [f"{b}/api/ingest/sms?token={tok}" for b in bases]},
        "dashboard_url": f"{bases[0]}/?token={tok}",
        "token": TOKEN if local else None,
        "db_path": str(config.DB_PATH) if local else None,
        "raw_counts": counts, "transactions": n_txn,
    }
