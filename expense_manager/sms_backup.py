"""Import historical SMS from an Android "SMS Backup & Restore" XML export."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from typing import BinaryIO

from . import db, ingest, parser


def import_xml(source: str | BinaryIO) -> dict:
    """Ingest bank SMS (inbox only) from the XML. Re-importing the same file is safe: duplicates are skipped."""
    stats: Counter = Counter()
    with db.connect() as conn:
        try:
            for _, el in ET.iterparse(source, events=("end",)):
                if el.tag != "sms":
                    continue
                stats["total_sms"] += 1
                sender, body = el.get("address", ""), el.get("body", "")
                if el.get("type") == "1" and body and parser.is_bank_sender(sender):  # type 1 = inbox
                    stats["bank_sms"] += 1
                    received = datetime.fromtimestamp(int(el.get("date", "0")) / 1000)
                    res = ingest.ingest_message(conn, source="sms", sender=sender, body=body,
                                                received_at=received, use_llm=False)
                    stats[res["status"]] += 1
                    if stats["bank_sms"] % 500 == 0:
                        conn.commit()  # let the dashboard see progress on big imports
                el.clear()
        except ET.ParseError as e:
            return {"ok": False, "error": f"Not a valid SMS Backup & Restore XML file: {e}", **stats}
    return {"ok": True, **stats}
