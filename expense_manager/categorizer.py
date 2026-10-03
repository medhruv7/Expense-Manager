"""Category assignment: user-learned rules > parser hints > built-in keywords > LLM > fallback."""

from __future__ import annotations

import re

from . import config, llm

CATEGORIES = [
    "Food & Dining", "Groceries", "Shopping", "Travel", "Fuel", "Bills & Utilities",
    "Entertainment", "Health", "Rent & Housing", "Education", "Personal Care", "Transfers",
    "Cash", "Loans & EMI", "Fees & Charges", "Investments", "Insurance", "Gifts & Donations", "Other",
    "Refund", "Income", "Self Transfer", "Credit Card Payment",
]

# Excluded from "spend": money that moves between your own pockets or isn't consumption.
NON_SPEND = {"Income", "Self Transfer", "Credit Card Payment", "Investments"}

DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "Groceries": ["blinkit", "grofers", "zepto", "bigbasket", "instamart", "dmart", "d mart",
                  "jiomart", "more retail", "reliance fresh", "spencer", "nature basket", "grocery",
                  "kirana", "milkbasket", "country delight", "licious", "freshtohome", "supermarket", "amazon fresh", "grocer", "groce", "swiggy instamart"],
    "Food & Dining": ["swiggy", "zomato", "restaurant", "cafe", "coffee", "starbucks", "dominos",
                      "domino", "mcdonald", "kfc", "pizza", "burger", "eatclub", "eat sure", "eatsure",
                      "haldiram", "chaayos", "barbeque", "dineout", "bakery", "food", "kitchen",
                      "biryani", "dhaba", "subway", "third wave", "blue tokai"],
    "Shopping": ["amazon", "flipkart", "myntra", "ajio", "meesho", "tatacliq", "tata cliq", "croma",
                 "reliance digital", "decathlon", "ikea", "pepperfry", "lifestyle", "westside",
                 "shoppers stop", "h&m", "zara", "uniqlo", "firstcry", "snapdeal", "lenskart"],
    "Travel": ["uber", "ola", "rapido", "irctc", "makemytrip", "goibibo", "cleartrip", "yatra",
               "indigo", "air india", "vistara", "akasa", "spicejet", "redbus", "abhibus", "metro",
               "fastag", "airbnb", "oyo", "booking.com", "agoda", "blu smart", "blusmart", "namma yatri",
               "railway", "parking", "toll"],
    "Fuel": ["petrol", "fuel", "hpcl", "bpcl", "iocl", "indian oil", "bharat petroleum",
             "hindustan petroleum", "shell", "nayara", "filling station", "service station"],
    "Bills & Utilities": ["airtel", "jio", "vodafone", "vi ", "bsnl", "electricity", "bescom",
                          "tata power", "adani electricity", "msedcl", "bses", "water", "gas",
                          "indane", "hp gas", "bharatgas", "broadband", "act fibernet", "hathway",
                          "recharge", "bbps", "billdesk", "dth", "tata play", "mahanagar gas",
                          "piped gas", "postpaid", "prepaid", "google one", "icloud", "apple.com"],
    "Entertainment": ["netflix", "spotify", "hotstar", "disney", "prime video", "primevideo",
                      "sonyliv", "zee5", "jiocinema", "youtube", "bookmyshow", "pvr", "inox",
                      "cinepolis", "steam", "playstation", "xbox", "gaana", "wynk", "audible"],
    "Health": ["pharmacy", "apollo", "1mg", "tata 1mg", "pharmeasy", "netmeds", "medplus",
               "hospital", "clinic", "diagnostic", "lab", "practo", "cult.fit", "cultfit", "cure.fit",
               "healthifyme", "dental", "medical", "chemist", "thyrocare", "gym"],
    "Rent & Housing": ["rent", "landlord", "nobroker", "nestaway", "society", "maintenance", "housing",
                       "mygate", "apartment", "urban company", "urbanclap"],
    "Education": ["school", "college", "university", "udemy", "coursera", "byju", "unacademy",
                  "upgrad", "tuition", "books", "kindle", "scaler", "leetcode"],
    "Personal Care": ["salon", "spa", "barber", "nykaa", "purplle", "mamaearth", "parlour", "grooming"],
    "Investments": ["zerodha", "groww", "upstox", "kuvera", "indmoney", "paytm money", "coin",
                    "mutual fund", "sip", "nps", "ppf", "smallcase", "angel one", "dhan", "icici direct",
                    "hdfc securities", "kotak securities", "bse limited", "nse clearing", "iccl", "nsccl"],
    "Insurance": ["insurance", "lic", "policybazaar", "acko", "digit", "hdfc ergo", "icici lombard",
                  "star health", "max life", "tata aia", "niva bupa", "care health"],
    "Fees & Charges": ["charges", "fee", "gst", "annual fee", "late payment", "interest", "penalty",
                       "finance charge", "sms charges", "amc"],
    "Gifts & Donations": ["donation", "charity", "temple", "gurudwara", "church", "ketto", "giveindia"],
}

def _kw_regex(kws: list[str]) -> re.Pattern:
    # Bank alerts glue words together ("SWIGGYPVTLTDFOOD", "AMAZONPAYINDIA"), so long keywords match as
    # substrings; short ones ("ola", "jio", "lic") need word boundaries to avoid hits like "chocolate".
    long_ = [re.escape(k.strip()) for k in kws if len(k.strip()) >= 5]
    short = [re.escape(k.strip()) for k in kws if len(k.strip()) < 5]
    alts = []
    if long_:
        alts.append("(?:" + "|".join(long_) + ")")
    if short:
        alts.append(r"(?<![a-z0-9])(?:" + "|".join(short) + r")(?![a-z0-9])")
    return re.compile("|".join(alts))


_KW_COMPILED = [(cat, _kw_regex(kws)) for cat, kws in DEFAULT_KEYWORDS.items()]


def keyword_category(*texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for cat, rx in _KW_COMPILED:
        if rx.search(blob):
            return cat
    return None


def is_self(name: str | None) -> bool:
    """Banks truncate names ("RAVI KUM", "Ravi"), so a >=4-char prefix of a configured self name counts."""
    if not name or not config.SELF_NAMES:
        return False
    n = re.sub(r"[^a-z ]", "", name.lower()).strip()
    n = re.sub(r"^(mr|mrs|ms|miss)\s+", "", n)
    return len(n) >= 4 and any(s.startswith(n) or n.startswith(s) for s in config.SELF_NAMES)


def rule_category(conn, *texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    rows = conn.execute("SELECT pattern, category FROM category_rules ORDER BY length(pattern) DESC").fetchall()
    for r in rows:
        if r["pattern"] and r["pattern"] in blob:
            return r["category"]
    return None


def categorize(conn, *, merchant: str | None, vpa: str | None, direction: str,
               hint: str | None, raw_text: str = "", use_llm: bool = True) -> tuple[str, str]:
    """Returns (category, source)."""
    if hint == "Credit Card Payment":
        return hint, "hint"
    cat = rule_category(conn, merchant, vpa)
    if cat:
        return cat, "rule"
    if is_self(merchant):
        return "Self Transfer", "auto"
    if hint in ("Cash", "Refund"):
        if hint == "Refund":
            kw = keyword_category(merchant, vpa)
            return (kw, "default") if kw else ("Refund", "hint")
        return hint, "hint"
    if direction == "credit":
        return "Income", "default"
    cat = keyword_category(merchant, vpa)
    if cat:
        return cat, "default"
    if hint:
        return hint, "hint"
    if use_llm and llm.enabled():
        cat = llm.categorize(merchant, vpa, raw_text, CATEGORIES)
        if cat:
            return cat, "llm"
    return "Other", "fallback"
