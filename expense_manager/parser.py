"""Deterministic parser for Indian bank / card / UPI alert SMS and emails.

Tuned for HDFC, Axis, ICICI and SBI Card formats, with generic fallbacks that
work for most other Indian banks. Returns a ParseResult with status:
  parsed   -> a transaction was extracted
  ignored  -> not a transaction (OTP, reminder, promo, failed txn, ...)
  unparsed -> looks like a transaction but couldn't be extracted reliably
"""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from . import config

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


@dataclass
class ParsedTxn:
    amount: float
    direction: str                 # debit | credit
    txn_time: datetime
    merchant: str | None = None
    payee_vpa: str | None = None
    bank: str | None = None
    account_last4: str | None = None
    instrument: str = "OTHER"      # UPI | CREDIT_CARD | DEBIT_CARD | CARD | NETBANKING | ATM | OTHER
    ref_no: str | None = None
    hint_category: str | None = None
    currency: str = "INR"
    orig_amount: float | None = None   # amount in `currency` when it isn't INR

    def to_dict(self) -> dict:
        d = asdict(self)
        d["txn_time"] = self.txn_time.strftime("%Y-%m-%d %H:%M:%S")
        return d


@dataclass
class ParseResult:
    status: str
    txn: ParsedTxn | None = None
    reason: str = ""


# --------------------------------------------------------------------------- helpers

def html_to_text(s: str) -> str:
    if "<" not in s or ">" not in s:
        return s
    s = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table)>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


def normalize(text: str) -> str:
    text = html_to_text(text or "").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


AMOUNT_RE = re.compile(
    r"(?<![a-z])(rs\.?|inr|₹|usd|us\$|\$|eur|€|gbp|£|aed|sgd|aud|cad|jpy|chf|thb|myr)\s*:?\s*"
    r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)
CURRENCY = {"rs": "INR", "rs.": "INR", "inr": "INR", "₹": "INR", "us$": "USD", "$": "USD", "€": "EUR", "£": "GBP"}
# Amount preceded (within the same sentence) by limit/balance wording is not the transaction amount.
NOT_TXN_AMOUNT_BEFORE = re.compile(
    r"(\bbal(ance)?|limit|\bavl|available|outstanding|\bdue|\bmin(imum)?)\b[^.\n]{0,30}$", re.I)

DEBIT_KW = re.compile(
    r"\b(debited|spent|sent|paid|withdrawn|withdrawal|deducted|charged|purchased?|"
    r"debit(?!\s*card)|txn of|transaction of|used for|"
    r"made an? (?:online )?(?:neft |imps |rtgs |upi )?payment)\b", re.I)
CREDIT_KW = re.compile(r"\b(credited|received|deposited|refund(?:ed)?|reversed|reversal|cashback)\b", re.I)

VPA_RE = re.compile(r"\b([A-Za-z0-9][\w.\-]{1,}@[A-Za-z][A-Za-z0-9]{1,})\b(?![.\-@]\w)")
EMAIL_RE = re.compile(r"\b[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+\b")

ACCOUNT_RES = [
    re.compile(r"(?:a/?c|acct|account|card)\b[^\n\d]{0,30}?[x*]+\s?(\d{3,6})", re.I),
    re.compile(r"\bending\s*(?:with\s*)?[x*]*\s?(\d{4})\b", re.I),
    re.compile(r"\b[x*]{2,}(\d{3,6})\b", re.I),
]

REF_RES = [
    re.compile(r"upi/p2[amb]/(\d{6,})", re.I),
    re.compile(
        r"(?:transaction reference number is|transaction id is|upi\s*ref(?:erence)?\s*(?:no\.?|number)?|"
        r"ref(?:erence)?\s*(?:no\.?|number|id)?|rrn|utr|txn\s*(?:id|no\.?)|imps|neft|upi)"
        r"\s*[:.#\-]?\s*(?:is\s*)?(\d{6,})", re.I),
]

BANK_PATTERNS = [
    ("SBI Card", r"sbi\s*card|sbicrd|sbicard|sbi credit card"),
    ("HDFC", r"hdfc"),
    ("ICICI", r"icici"),
    ("Axis", r"\baxis\b|axisbk|axis\s*bank|axbank"),
    ("SBI", r"\bsbi\b|sbiinb|sbipsg|sbibnk|state bank"),
    ("Kotak", r"kotak|kotakb"),
    ("IDFC First", r"idfc"),
    ("Yes Bank", r"yes\s*bank|yesbnk"),
    ("IndusInd", r"indusind|indusb"),
    ("Federal", r"federal\s*bank|fedbnk"),
    ("AU Bank", r"au\s*small|aubank"),
    ("RBL", r"\brbl\b|rblbnk"),
    ("PNB", r"\bpnb\b|pnbsms"),
    ("Bank of Baroda", r"bank of baroda|bobtxn|bobsms"),
    ("Amex", r"american express|\bamex\b"),
    ("OneCard", r"onecard"),
]

TERM = r"(?:\s+on\s+\d|\s+via\s|\s*\(|\.\s|\.$|\s+avl|\s+ref\b|\s+upi\b|\n|$)"
MERCHANT_PATTERNS: list[tuple[re.Pattern, str | None]] = [
    (re.compile(r"UPI/P2[AMB]/\d+/([^/\n]+)", re.I), None),                                   # Axis
    (re.compile(r"\b(?:debited|credited) with (?:INR|Rs\.?)\s*[\d,.]+ on [^\n]{0,40}? by ([^\n]+?)(?:\.\s|\.$|\n|$)",
                re.I), None),                                                                 # Axis email
    (re.compile(r"\b((?:NEFT|IMPS|RTGS|NBSM|ACH|NACH|ECS|INB|BIL|MMT)/\S+)", re.I), None),        # Axis ref path
    (re.compile(r"\binfo\s*:\s*([^\n.]+)", re.I), None),                                         # ICICI card
]
AUTHORITATIVE = len(MERCHANT_PATTERNS)  # patterns above are structured payee fields
MERCHANT_PATTERNS += [
    (re.compile(r"\bVPA\s+[\w.\-]+@[\w.\-]+\s+([A-Za-z][^\n]*?)\s+on\s+\d", re.I), None),       # HDFC email
    (re.compile(r";\s*([^;\n]+?)\s+credited", re.I), "debit"),                                  # ICICI
    (re.compile(r"\btowards\s+([^\n]+?)\s+(?:from|on)\s", re.I), "debit"),                        # ICICI net banking
    (re.compile(r"\bon\s+\d{1,2}[-/ ]?\w{2,3}[-/ ]?\d{2,4}\s+(?:on|at)\s+([^\n]+?)" + TERM, re.I), "debit"),
    (re.compile(r"\bat\s+([^\n]+?)" + TERM, re.I), "debit"),                                   # cards
    (re.compile(r"\bto\s*:?\s+([^\n]+?)" + TERM, re.I), "debit"),                               # HDFC / generic
    (re.compile(r"\bfrom\s+([^\n]+?)" + TERM, re.I), "credit"),
    (re.compile(r"\bby\s+([^\n]+?)" + TERM, re.I), "credit"),
    (re.compile(r"\binfo\s*[:\-]\s*([^\n.]+)", re.I), None),
]
BAD_MERCHANT = re.compile(
    r"^(your|you|a/?c|acct|account|the|beneficiary|card|us|dispute|block|report|sms|call|inform|wish|thank|we|reach|please|should|"
    r"hdfc bank a/?c|linked|check|click)\b|a/c|acct|[x*]{2,}\d|customer care|not you|https?:|www\.|"
    r"@[\w\-]+\.(?:com|in|co)\b", re.I)
PATH_NOISE = re.compile(r"(?i)^(p2[amb]|neft|imps|rtgs|nbsm|upi|ach|nach|ecs|inb|bil|mmt)$")

IGNORE_RULES = [
    (re.compile(r"\botp\b|one[\s-]?time[\s-]?password|verification code", re.I), "OTP"),
    (re.compile(r"\bwill be (?:debited|charged|deducted|credited)|\bis due\b|due date|payment due|"
                r"min(?:imum)?\.? amount due|total amount due|statement (?:is |has been )?generated|"
                r"\bupcoming\b|\bscheduled\b", re.I), "reminder/statement"),
    (re.compile(r"has requested|collect request|requested (?:money|rs|inr|₹)|"
                r"request(?:ing)? (?:money|payment)", re.I), "payment request"),
    (re.compile(r"credited to your loan (?:a/?c|account)", re.I), "loan repayment (debit tracked separately)"),
    (re.compile(r"thank you for applying|application (?:details|no\.?|number|id)\b|key fact statement", re.I),
     "application/offer"),
    (re.compile(r"mandate (?:is |has been )?(?:created|registered|set ?up|successfully registered)|"
                r"autopay (?:is |has been )?(?:set|registered|activated|enabled)", re.I), "mandate setup"),
]
BOILERPLATE_RE = re.compile(
    r"\b(?:never|do not|don'?t|not to|avoid)\s+(?:ever\s+)?(?:share|disclose|reveal)[^.\n]*|"
    r"safe banking tip[^\n]*", re.I)
FAILED_RE = re.compile(r"\bdeclined\b|\bfailed\b|unsuccessful|could not be processed|not been processed", re.I)
PROMO_RE = re.compile(r"pre-?approved|\boffers?\b|apply now|eligible for|loan (?:of|up ?to)|"
                      r"get up ?to|limit (?:increase|enhance)|click here", re.I)

CC_PAYMENT_RE = re.compile(
    r"credit card (?:bill|payment)|card payment|cc payment|sbi ?card|\bcred\b|cred club|"
    r"payment (?:of .{0,30})?received towards|received (?:your )?payment|payment received|"
    r"thank you for (?:the |your )?payment", re.I)


def _find_bank(sender: str, text: str) -> str | None:
    s = (sender or "").lower()
    for name, pat in BANK_PATTERNS:
        if re.search(pat, s):
            return name
    clean = EMAIL_RE.sub(" ", VPA_RE.sub(" ", text.lower()))
    best = None
    for name, pat in BANK_PATTERNS:
        m = re.search(pat, clean)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else None


def is_bank_sender(sender: str) -> bool:
    s = (sender or "").lower()
    return any(re.search(pat, s) for _, pat in BANK_PATTERNS)


def _pick_amount(text: str, anchor: int) -> tuple[float, str] | None:
    best = None
    for m in AMOUNT_RE.finditer(text):
        before = text[max(0, m.start() - 45):m.start()]
        if NOT_TXN_AMOUNT_BEFORE.search(before):
            continue
        try:
            val = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        if val <= 0:
            continue
        cur = m.group(1).lower()
        cur = CURRENCY.get(cur, cur.upper())
        dist = abs(m.start() - anchor)
        if best is None or dist < best[0]:
            best = (dist, val, cur)
    return (best[1], best[2]) if best else None


def _find_account(text: str) -> str | None:
    for r in ACCOUNT_RES:
        m = r.search(text)
        if m:
            return m.group(1)[-4:]
    return None


def _find_ref(text: str) -> str | None:
    for r in REF_RES:
        m = r.search(text)
        if m:
            return m.group(1)
    return None


# (compact lowercase substring, display name); first match wins.
BRANDS = [
    ("instamart", "Swiggy Instamart"), ("swiggypvtltdgroc", "Swiggy Instamart"), ("swiggy", "Swiggy"),
    ("zomato", "Zomato"), ("blinkit", "Blinkit"), ("blinkmerce", "Blinkit"), ("grofers", "Blinkit"),
    ("zepto", "Zepto"), ("bigbasket", "BigBasket"), ("amazonpayingrocer", "Amazon Fresh"),
    ("amazonpaygrocer", "Amazon Fresh"), ("amazonfresh", "Amazon Fresh"), ("amazonpayindia", "Amazon Pay"),
    ("amazon", "Amazon"), ("flipkart", "Flipkart"), ("myntra", "Myntra"), ("uberindia", "Uber"),
    ("ubertrip", "Uber"), ("rapido", "Rapido"), ("olacabs", "Ola"), ("irctc", "IRCTC"), ("indigo", "IndiGo"),
    ("makemytrip", "MakeMyTrip"), ("groww", "Groww"), ("zerodha", "Zerodha"), ("mobikwik", "MobiKwik"),
    ("munchmart", "MunchMart"), ("tataplay", "Tata Play"), ("jubilantfood", "Domino's"), ("dominos", "Domino's"),
    ("livpure", "Livpure"), ("googlepl", "Google Play"), ("navifin", "Navi"), ("netflix", "Netflix"),
    ("spotify", "Spotify"), ("bookmyshow", "BookMyShow"), ("starbucks", "Starbucks"), ("dmart", "DMart"),
    ("airtel", "Airtel"), ("bescom", "BESCOM"), ("cred club", "CRED"), ("credclub", "CRED"),
]


def canonical_merchant(name: str | None) -> str | None:
    if not name:
        return name
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for k, display in BRANDS:
        if k.replace(" ", "") in key:
            return display
    return name


GATEWAY_PREFIX = re.compile(r"^(?:(?:RAZ|PYU|PTM|RSP)(?=[A-Za-z]{4})|(?:PAY|ZES|IFT|CCA)(?=[A-Z][a-z]))")
STRIP = " .,:;-*'\"()"


def _clean_merchant(raw: str) -> str | None:
    s = re.sub(r"\s+", " ", raw).strip(STRIP)
    if not s or BAD_MERCHANT.search(s):
        return None
    if "/" in s:  # bank reference paths like NEFT/IN2261.../ACME CORP -> ACME CORP
        parts = [p.strip() for p in s.split("/") if re.search(r"[A-Za-z]{3,}", p) and not PATH_NOISE.match(p.strip())]
        s = parts[-1] if parts else ""
    s = re.sub(r"\b(on|via|ref|upi|vpa)$", "", s, flags=re.I).strip(STRIP)
    s = re.sub(r"^(vpa|m/s\.?|merchant)\s+", "", s, flags=re.I).strip(STRIP)
    s = GATEWAY_PREFIX.sub("", s).strip(STRIP)  # payment-gateway codes: RAZ*, PYU*, PTM*, ...
    if not s or len(s) < 2 or BAD_MERCHANT.search(s):
        return None
    if sum(c.isdigit() for c in s) > len(s) / 2:
        return None
    s = s[:60]
    if s.isupper() or s.islower():
        s = s.title()
    return canonical_merchant(s)


def _find_merchant(text: str, direction: str) -> tuple[str | None, str | None]:
    vpa_match = VPA_RE.search(EMAIL_RE.sub(" ", text))
    vpa = vpa_match.group(1).lower() if vpa_match else None
    merchant = None
    for i, (pat, only_dir) in enumerate(MERCHANT_PATTERNS):
        if only_dir and only_dir != direction:
            continue
        if i < AUTHORITATIVE and pat.search(text) and not any(
                _clean_merchant(VPA_RE.sub(" ", m.group(1))) for m in pat.finditer(text)):
            break  # structured payee field exists but carries no name (e.g. NEFT/IR/<code>/R); don't guess
        for m in pat.finditer(text):
            cand = m.group(1)
            v = VPA_RE.search(cand)
            if v:
                vpa = vpa or v.group(1).lower()
                cand = VPA_RE.sub(" ", cand)
            name = _clean_merchant(cand)
            if name:
                merchant = name
                break
        if merchant:
            break
    if not merchant and vpa:
        local = re.sub(r"[\d]{4,}", "", vpa.split("@")[0]).strip(".-_")
        merchant = _clean_merchant(local.replace(".", " ")) if local else None
    return merchant, vpa


def _mk_date(y: int, mo: int, d: int) -> datetime | None:
    if y < 100:
        y += 2000
    try:
        return datetime(y, mo, d)
    except ValueError:
        return None


def _find_datetime(text: str, received: datetime) -> tuple[datetime | None, bool]:
    cands: list[tuple[int, datetime]] = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        dt = _mk_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if dt:
            cands.append((m.start(), dt))
    for m in re.finditer(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b", text):
        dt = _mk_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if dt:
            cands.append((m.start(), dt))
    for m in re.finditer(r"\b(\d{1,2})[-\s]?([a-z]{3})[a-z]*[-\s,]*(\d{4}|\d{2})\b", text, re.I):
        mo = MONTHS.get(m.group(2).lower())
        if mo:
            dt = _mk_date(int(m.group(3)), mo, int(m.group(1)))
            if dt:
                cands.append((m.start(), dt))
    for m in re.finditer(r"\b([a-z]{3})[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b", text, re.I):
        mo = MONTHS.get(m.group(1).lower())
        if mo:
            dt = _mk_date(int(m.group(3)), mo, int(m.group(2)))
            if dt:
                cands.append((m.start(), dt))
    for m in re.finditer(r"\bon\s+(\d{1,2})[-/](\d{1,2})\b(?![-/]\d)", text, re.I):
        dt = _mk_date(received.year, int(m.group(2)), int(m.group(1)))
        if dt:
            cands.append((m.start(), dt))

    date = None
    for _, dt in sorted(cands, key=lambda c: c[0]):
        if received - timedelta(days=10) <= dt <= received + timedelta(days=1):
            date = dt
            break
    if not date:
        return None, False

    tm = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?\s*([ap]\.?m\.?)?", text, re.I)
    if tm:
        h, mi, se = int(tm.group(1)), int(tm.group(2)), int(tm.group(3) or 0)
        ampm = (tm.group(4) or "").lower().replace(".", "")
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return date.replace(hour=h, minute=mi, second=se), True
    return date, False


def _instrument(tl: str, bank: str | None, vpa: str | None, ref: str | None = None) -> str:
    if "credit card" in tl or bank in ("SBI Card", "Amex", "OneCard") or (
            "card" in tl and re.search(r"avl\.? limit|available limit|credit limit", tl)):
        return "CREDIT_CARD"
    if "debit card" in tl:
        return "DEBIT_CARD"
    if re.search(r"\batm\b|cash withdrawal|withdrawn", tl):
        return "ATM"
    if "upi" in tl or vpa:
        return "UPI"
    if re.search(r"\b(neft|imps|rtgs|nach|ecs|bbps|billpay)\b|net ?banking|internet banking", tl):
        return "NETBANKING"
    if "card" in tl:
        return "CARD"
    if ref and len(ref) == 12:  # UPI RRNs are 12 digits
        return "UPI"
    return "OTHER"


def derive_hint(text: str, direction: str, instrument: str) -> str | None:
    tl = text.lower()
    if CC_PAYMENT_RE.search(text) and (
            (direction == "credit" and instrument == "CREDIT_CARD")
            or (direction == "debit" and instrument != "CREDIT_CARD")):
        return "Credit Card Payment"
    if instrument == "ATM":
        return "Cash"
    if direction == "credit" and re.search(r"refund|revers|cashback", tl):
        return "Refund"
    if direction == "debit" and re.search(r"(?:^|[_\s/])emi(?:[_\s/.]|$)", tl):
        return "Loans & EMI"
    if re.search(r"(?:upi|imps)/p2a/", tl):
        return "Transfers"
    return None


# --------------------------------------------------------------------------- main entry

def parse(text: str, sender: str = "", subject: str = "", received_at: datetime | None = None,
          source: str = "sms") -> ParseResult:
    received = received_at or datetime.now()
    full = normalize(f"{subject}\n{text}" if subject else text)
    if not full:
        return ParseResult("ignored", reason="empty")

    if source == "sms" and sender and not is_bank_sender(sender):
        return ParseResult("ignored", reason="sender is not a known bank")

    kw_d, kw_c = DEBIT_KW.search(full), CREDIT_KW.search(full)
    first = min([m for m in (kw_d, kw_c) if m], key=lambda m: m.start(), default=None)

    # Long emails: focus on the region around the first transaction keyword.
    if first and len(full) > 1200:
        start = max(0, first.start() - 400)
        t = full[start:first.start() + 700]
        anchor = first.start() - start
    else:
        t = full
        anchor = first.start() if first else 0

    # Security boilerplate ("Never share your CVV, PIN, OTP…") must not trigger the OTP filter.
    t_check = BOILERPLATE_RE.sub(" ", t)
    for rx, reason in IGNORE_RULES:
        if rx.search(t_check):
            return ParseResult("ignored", reason=reason)

    picked = _pick_amount(t, anchor)
    amount, currency = picked if picked else (None, "INR")
    account = _find_account(t)
    ref = _find_ref(t)

    if FAILED_RE.search(t_check) and (not first or first is kw_d):
        return ParseResult("ignored", reason="failed/declined transaction")
    if not first and amount is None:
        return ParseResult("ignored", reason="no transaction info")
    # Genuine alerts always cite an account/card number or a reference; bank marketing doesn't.
    if not account and not ref and (source == "email" or PROMO_RE.search(t)):
        return ParseResult("ignored", reason="no account/card/reference (likely promotional)")
    if source == "email" and not first:
        return ParseResult("ignored", reason="no debit/credit wording")
    if not first or amount is None:
        return ParseResult("unparsed", reason="missing amount or debit/credit keyword")

    direction = "debit" if first is kw_d else "credit"
    tl = t.lower()

    bank = _find_bank(sender, full)
    merchant, vpa = _find_merchant(t, direction)
    instrument = _instrument(tl, bank, vpa, ref)

    dt, has_time = _find_datetime(t, received)
    if dt is None or (not has_time and dt.date() == received.date()):
        txn_time = received
    elif not has_time:
        txn_time = dt.replace(hour=12)
    else:
        txn_time = dt

    hint = derive_hint(t, direction, instrument)

    if not merchant:
        merchant = "Loan EMI" if hint == "Loans & EMI" else None
        merchant = merchant or {"ATM": "ATM Withdrawal", "NETBANKING": "Bank Transfer"}.get(instrument)
        if hint == "Credit Card Payment":
            merchant = merchant or "Card Bill Payment"

    orig = None
    if currency != "INR":
        orig, amount = amount, amount * config.fx_rate(currency)
    return ParseResult("parsed", ParsedTxn(
        currency=currency, orig_amount=orig, amount=round(amount, 2), direction=direction, txn_time=txn_time.replace(microsecond=0),
        merchant=merchant, payee_vpa=vpa, bank=bank, account_last4=account,
        instrument=instrument, ref_no=ref, hint_category=hint,
    ))
