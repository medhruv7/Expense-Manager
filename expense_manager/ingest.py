"""Ingestion pipeline: raw message -> parse (regex, then optional LLM) -> dedupe -> categorise -> store."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime

from . import categorizer, config, llm, parser

FMT = "%Y-%m-%d %H:%M:%S"
DEDUP_WINDOW_MIN = 240
SELF_TRANSFER_WINDOW_MIN = 30


def fmt(dt: datetime) -> str:
    return dt.strftime(FMT)


def ingest_message(conn, *, source: str, body: str, sender: str = "", subject: str = "",
                   received_at: datetime | None = None, external_id: str | None = None,
                   use_llm: bool = True) -> dict:
    received = (received_at or datetime.now()).replace(microsecond=0)
    if not external_id:
        h = hashlib.sha1(f"{sender}|{subject}|{body}|{received:%Y%m%d%H%M}".encode()).hexdigest()
        external_id = f"{source}:{h}"
    try:
        cur = conn.execute(
            "INSERT INTO raw_messages(source, sender, subject, body, received_at, external_id) VALUES(?,?,?,?,?,?)",
            (source, sender, subject, body, fmt(received), external_id),
        )
    except sqlite3.IntegrityError:
        return {"status": "exists"}
    return process_raw(conn, cur.lastrowid, use_llm=use_llm)


def _txn_from_llm(out: dict, text: str, sender: str, received: datetime) -> parser.ParsedTxn:
    dt = received
    if out.get("date"):
        try:
            d = datetime.strptime(out["date"], "%Y-%m-%d")
            t = datetime.strptime(out["time"], "%H:%M").time() if out.get("time") else None
            if abs((d.date() - received.date()).days) <= 10:
                dt = datetime.combine(d.date(), t) if t else (received if d.date() == received.date()
                                                              else d.replace(hour=12))
        except (ValueError, TypeError):
            pass
    instrument = out.get("instrument") or "OTHER"
    direction = out["direction"]
    merchant = parser._clean_merchant(out["merchant"]) if out.get("merchant") else None
    return parser.ParsedTxn(
        amount=round(out["amount"], 2), direction=direction, txn_time=dt, merchant=merchant,
        payee_vpa=(out.get("payee_vpa") or None), bank=parser._find_bank(sender, text),
        account_last4=(str(out["account_last4"])[-4:] if out.get("account_last4") else None),
        instrument=instrument, ref_no=(str(out["ref_no"]) if out.get("ref_no") else None),
        hint_category=parser.derive_hint(text, direction, instrument),
    )


def process_raw(conn, raw_id: int, use_llm: bool = True, force_llm: bool = False) -> dict:
    row = conn.execute("SELECT * FROM raw_messages WHERE id=?", (raw_id,)).fetchone()
    received = datetime.strptime(row["received_at"], FMT)
    res = parser.parse(row["body"], row["sender"] or "", row["subject"] or "", received, row["source"])
    parsed_by = "regex"

    sender_rejected = res.status == "ignored" and res.reason == "sender is not a known bank"
    if use_llm and llm.enabled() and not sender_rejected and (res.status == "unparsed" or force_llm):
        text = parser.normalize(f"{row['subject'] or ''}\n{row['body']}")
        out = llm.parse(text, received)
        if out and out.get("is_transaction"):
            res = parser.ParseResult("parsed", _txn_from_llm(out, text, row["sender"] or "", received))
            parsed_by = "llm"
        elif out is not None and out.get("is_transaction") is False:
            res = parser.ParseResult("ignored", reason="LLM: not a transaction")

    if res.status != "parsed":
        conn.execute("UPDATE raw_messages SET status=?, note=? WHERE id=?", (res.status, res.reason, raw_id))
        return {"status": res.status, "reason": res.reason, "raw_id": raw_id}

    txn = res.txn
    dup = find_duplicate(conn, txn, row["source"])
    if dup:
        merge_into(conn, dup, txn, row["source"])
        conn.execute("UPDATE raw_messages SET status='duplicate', transaction_id=?, note=? WHERE id=?",
                     (dup["id"], "merged with existing transaction", raw_id))
        return {"status": "duplicate", "transaction_id": dup["id"], "raw_id": raw_id}

    notes = None
    if txn.currency != "INR" and txn.orig_amount:
        notes = (f"Foreign txn: {txn.currency} {txn.orig_amount:,.2f} ≈ ₹{txn.amount:,.2f} "
                 f"at ~{config.fx_rate(txn.currency)}/{txn.currency}; card statement may differ slightly")
    category, cat_src = categorizer.categorize(
        conn, merchant=txn.merchant, vpa=txn.payee_vpa, direction=txn.direction,
        hint=txn.hint_category, raw_text=row["body"], use_llm=use_llm)
    cur = conn.execute(
        """INSERT INTO transactions(txn_time, amount, direction, merchant, payee_vpa, category, category_source,
               bank, account_last4, instrument, ref_no, source, sources, parsed_by, notes)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (fmt(txn.txn_time), txn.amount, txn.direction, txn.merchant, txn.payee_vpa, category, cat_src,
         txn.bank, txn.account_last4, txn.instrument, txn.ref_no, row["source"], row["source"], parsed_by, notes),
    )
    tid = cur.lastrowid
    conn.execute("UPDATE raw_messages SET status='parsed', transaction_id=?, note=NULL WHERE id=?", (tid, raw_id))
    detect_self_transfer(conn, tid)
    return {"status": "parsed", "transaction_id": tid, "raw_id": raw_id, "parsed_by": parsed_by,
            "transaction": dict(conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone())}


def _acct_compatible(a: str | None, b: str | None) -> bool:
    return not a or not b or a[-3:] == b[-3:]


def find_duplicate(conn, txn: parser.ParsedTxn, source: str):
    rows = conn.execute(
        """SELECT * FROM transactions WHERE amount=? AND direction=?
           AND ABS(julianday(txn_time) - julianday(?)) * 1440 <= ? ORDER BY id""",
        (txn.amount, txn.direction, fmt(txn.txn_time), DEDUP_WINDOW_MIN),
    ).fetchall()
    for r in rows:
        if txn.ref_no and r["ref_no"]:
            if txn.ref_no == r["ref_no"]:
                return r
            continue
        if not _acct_compatible(txn.account_last4, r["account_last4"]):
            continue
        # Same channel twice with no distinguishing ref = two genuine purchases (e.g. two ₹20 chais).
        if source in (r["sources"] or r["source"]).split(","):
            continue
        return r
    return None


def merge_into(conn, existing, txn: parser.ParsedTxn, source: str) -> None:
    sources = set((existing["sources"] or existing["source"]).split(",")) | {source}
    generic = {None, "", "Bank Transfer", "Card Bill Payment"}
    conn.execute(
        """UPDATE transactions SET
             merchant = ?, payee_vpa = COALESCE(payee_vpa, ?), ref_no = COALESCE(ref_no, ?),
             account_last4 = COALESCE(account_last4, ?), bank = COALESCE(bank, ?),
             sources = ?, updated_at = datetime('now','localtime')
           WHERE id = ?""",
        (txn.merchant if existing["merchant"] in generic and txn.merchant else existing["merchant"],
         txn.payee_vpa, txn.ref_no, txn.account_last4, txn.bank, ",".join(sorted(sources)), existing["id"]),
    )


def detect_self_transfer(conn, tid: int) -> None:
    t = conn.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()
    if not t["account_last4"] or t["instrument"] == "CREDIT_CARD" or t["category_source"] in ("user", "rule"):
        return
    opp = "credit" if t["direction"] == "debit" else "debit"
    m = conn.execute(
        """SELECT id FROM transactions WHERE id != ? AND amount = ? AND direction = ?
             AND account_last4 IS NOT NULL AND substr(account_last4, -3) != substr(?, -3)
             AND COALESCE(instrument, '') != 'CREDIT_CARD'
             AND COALESCE(category_source, '') NOT IN ('user', 'rule')
             AND category != 'Self Transfer'
             AND ABS(julianday(txn_time) - julianday(?)) * 1440 <= ?
           ORDER BY ABS(julianday(txn_time) - julianday(?)) LIMIT 1""",
        (tid, t["amount"], opp, t["account_last4"], t["txn_time"], SELF_TRANSFER_WINDOW_MIN, t["txn_time"]),
    ).fetchone()
    if m:
        conn.execute(
            "UPDATE transactions SET category='Self Transfer', category_source='auto', "
            "updated_at=datetime('now','localtime') WHERE id IN (?, ?)", (tid, m["id"]))


def add_rule(conn, pattern: str, category: str) -> int:
    """Learn a merchant -> category mapping and apply it to existing, non-user-edited transactions."""
    pattern = pattern.strip().lower()
    if not pattern:
        return 0
    conn.execute(
        "INSERT INTO category_rules(pattern, category) VALUES(?, ?) "
        "ON CONFLICT(pattern) DO UPDATE SET category=excluded.category", (pattern, category))
    cur = conn.execute(
        """UPDATE transactions SET category=?, category_source='rule', updated_at=datetime('now','localtime')
           WHERE (instr(lower(COALESCE(merchant,'')), ?) > 0 OR instr(lower(COALESCE(payee_vpa,'')), ?) > 0)
             AND COALESCE(category_source,'') != 'user'
             AND category NOT IN ('Credit Card Payment', 'Self Transfer')""",
        (category, pattern, pattern))
    return cur.rowcount


def recategorize(conn, only_fallback: bool = True, use_llm: bool = True) -> int:
    q = "SELECT t.*, (SELECT body FROM raw_messages WHERE transaction_id=t.id LIMIT 1) AS body FROM transactions t " \
        "WHERE COALESCE(category_source,'') NOT IN ('user','auto','hint')"
    if only_fallback:
        q += " AND category = 'Other'"
    n = 0
    for t in conn.execute(q).fetchall():
        hint = parser.derive_hint(t["body"] or "", t["direction"], t["instrument"] or "OTHER") if t["body"] else None
        cat, src = categorizer.categorize(conn, merchant=t["merchant"], vpa=t["payee_vpa"],
                                          direction=t["direction"], hint=hint, raw_text=t["body"] or "",
                                          use_llm=use_llm)
        if cat != t["category"]:
            conn.execute("UPDATE transactions SET category=?, category_source=? WHERE id=?", (cat, src, t["id"]))
            n += 1
    return n
