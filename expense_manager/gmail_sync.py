"""Pull bank / card alert emails from Gmail over IMAP (uses a Google App Password)."""

from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from . import config, db, ingest

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(config.GMAIL_USER and config.GMAIL_APP_PASSWORD)


def _decode(v: str | None) -> str:
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return v


def _body(msg: email.message.Message) -> str:
    plain, htm = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        ctype = part.get_content_type()
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and htm is None:
            htm = text
    # Bank emails often ship a stub text/plain part; prefer HTML when plain is suspiciously short.
    if plain and (not htm or len(plain.strip()) > 80):
        return plain
    return htm or plain or ""


def _local(dt: datetime) -> datetime:
    if dt.tzinfo:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def sync(days: int | None = None) -> dict:
    if not configured():
        return {"ok": False, "error": "Gmail not configured (set GMAIL_USER and GMAIL_APP_PASSWORD in .env)"}

    with db.connect() as conn:
        last = db.get_meta(conn, "gmail_last_sync")
    if days is not None:
        since = datetime.now() - timedelta(days=days)
    elif last:
        since = datetime.strptime(last, ingest.FMT) - timedelta(days=2)
    else:
        since = datetime.now() - timedelta(days=config.GMAIL_BACKFILL_DAYS)

    stats = {"fetched": 0, "parsed": 0, "duplicate": 0, "ignored": 0, "unparsed": 0, "exists": 0}
    started = datetime.now()
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        typ, _ = imap.select('"[Gmail]/All Mail"', readonly=True)
        if typ != "OK":
            imap.select("INBOX", readonly=True)

        uids: set[bytes] = set()
        for f in config.GMAIL_SENDER_FILTERS:
            typ, data = imap.uid("search", None, f'(FROM "{f}" SINCE {since:%d-%b-%Y})')
            if typ == "OK" and data and data[0]:
                uids.update(data[0].split())

        ordered = sorted(uids, key=int)
        for i in range(0, len(ordered), 50):  # batched fetch: one round-trip per 50 messages
            typ, data = imap.uid("fetch", b",".join(ordered[i:i + 50]), "(UID BODY.PEEK[])")
            if typ != "OK" or not data:
                continue
            for part in data:
                if not isinstance(part, tuple):
                    continue
                msg = email.message_from_bytes(part[1])
                try:
                    received = _local(parsedate_to_datetime(msg["Date"]))
                except Exception:
                    received = datetime.now()
                ext_id = (msg.get("Message-ID") or "").strip()
                if not ext_id:
                    m = re.search(rb"UID (\d+)", part[0])
                    ext_id = f"gmail-uid:{m.group(1).decode() if m else hash(part[1])}"
                with db.connect() as conn:
                    res = ingest.ingest_message(
                        conn, source="email", body=_body(msg), sender=_decode(msg.get("From")),
                        subject=_decode(msg.get("Subject")), received_at=received, external_id=ext_id)
                stats["fetched"] += 1
                stats[res["status"]] = stats.get(res["status"], 0) + 1
    except imaplib.IMAP4.error as e:
        log.error("Gmail sync failed: %s", e)
        return {"ok": False, "error": f"IMAP error: {e}", **stats}
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    with db.connect() as conn:
        db.set_meta(conn, "gmail_last_sync", ingest.fmt(started))
    log.info("Gmail sync: %s", stats)
    return {"ok": True, "since": f"{since:%Y-%m-%d}", **stats}
