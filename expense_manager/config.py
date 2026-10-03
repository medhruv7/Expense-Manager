import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# SQLite lives outside OneDrive by default: cloud-sync clients can corrupt live SQLite files.
DATA_DIR = Path(os.path.expanduser(os.getenv("EM_DATA_DIR", "~/.expense-manager")))
DB_PATH = DATA_DIR / "expenses.db"

HOST = os.getenv("EM_HOST", "0.0.0.0")
PORT = int(os.getenv("EM_PORT", "8642"))

GMAIL_USER = os.getenv("GMAIL_USER", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
GMAIL_POLL_MINUTES = int(os.getenv("GMAIL_POLL_MINUTES", "15"))
GMAIL_BACKFILL_DAYS = int(os.getenv("GMAIL_BACKFILL_DAYS", "90"))
GMAIL_SENDER_FILTERS = [
    s.strip()
    for s in os.getenv(
        "GMAIL_SENDER_FILTERS",
        "hdfcbank,axisbank,axis.bank,icicibank,icici.bank,sbicard",
    ).split(",")
    if s.strip()
]

# Your own name(s) as banks print them; payments to/from these are self-transfers, not spend.
SELF_NAMES = [s.strip().lower() for s in os.getenv("EM_SELF_NAMES", "").split(",") if s.strip()]

# Approximate INR rates for foreign-currency card alerts (the bank's final INR charge may differ slightly).
FX_RATES = {"USD": 88.0, "EUR": 103.0, "GBP": 118.0, "AED": 24.0, "SGD": 68.0, "AUD": 58.0, "CAD": 64.0,
            "JPY": 0.6, "CHF": 110.0, "THB": 2.7, "MYR": 20.0}
for _pair in os.getenv("FX_RATES", "").split(","):
    if "=" in _pair:
        _k, _v = _pair.split("=", 1)
        FX_RATES[_k.strip().upper()] = float(_v)


def fx_rate(currency: str) -> float:
    return FX_RATES.get(currency.upper(), 1.0)


# Tailscale logins that may open the dashboard via `tailscale serve` without the token
# (default: the login that owns this Mac's Tailscale node).
TAILSCALE_USERS = [s.strip().lower() for s in os.getenv("EM_TAILSCALE_USERS", "").split(",") if s.strip()]

LLM_ENABLED = _bool("LLM_ENABLED")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.1")


def get_token() -> str:
    """Shared secret for the SMS webhook and non-localhost dashboard access."""
    env = os.getenv("EM_TOKEN", "").strip()
    if env:
        return env
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / "token"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(18))
        f.chmod(0o600)
    return f.read_text().strip()
