"""Optional LLM fallback via any OpenAI-compatible endpoint (Ollama, OpenAI, LM Studio, ...).

Only used for (a) messages the regex parser couldn't handle and (b) merchants no rule matches.
Disabled unless LLM_ENABLED=true.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

import httpx

from . import config

log = logging.getLogger(__name__)


def enabled() -> bool:
    return config.LLM_ENABLED


def _chat(system: str, user: str, timeout: float = 60) -> dict | None:
    try:
        r = httpx.post(
            f"{config.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={
                "model": config.LLM_MODEL,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=timeout,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:  # network, auth, bad JSON: fall back silently
        log.warning("LLM call failed: %s", e)
        return None


PARSE_SYSTEM = """You extract financial transactions from Indian bank/credit-card/UPI alert messages.
Reply ONLY with JSON:
{"is_transaction": bool, "amount": number, "direction": "debit"|"credit",
 "merchant": string|null, "payee_vpa": string|null, "account_last4": string|null,
 "instrument": "UPI"|"CREDIT_CARD"|"DEBIT_CARD"|"NETBANKING"|"ATM"|"OTHER",
 "ref_no": string|null, "date": "YYYY-MM-DD"|null, "time": "HH:MM"|null}
is_transaction is false for OTPs, reminders, due-date notices, offers, payment requests and failed/declined transactions.
"direction" is debit when money left the user's account/card, credit when money came in."""


def parse(text: str, received: datetime) -> dict | None:
    out = _chat(PARSE_SYSTEM, f"Message received at {received:%Y-%m-%d %H:%M}:\n\n{text[:3000]}")
    if not out or not out.get("is_transaction"):
        return out
    try:
        out["amount"] = float(str(out.get("amount")).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if out.get("direction") not in ("debit", "credit") or out["amount"] <= 0:
        return None
    return out


def categorize(merchant: str | None, vpa: str | None, text: str, categories: list[str]) -> str | None:
    allowed = [c for c in categories if c not in ("Income", "Refund")]
    out = _chat(
        "You categorise Indian consumer expenses. Reply ONLY with JSON {\"category\": <one of the allowed>}. "
        "Allowed: " + ", ".join(allowed),
        f"Merchant: {merchant or '-'}\nUPI VPA: {vpa or '-'}\nAlert text: {text[:800]}",
        timeout=30,
    )
    cat = (out or {}).get("category")
    return cat if cat in allowed else None
