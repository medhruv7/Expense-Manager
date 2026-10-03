from email.message import EmailMessage

from expense_manager import config, db, gmail_sync


class FakeIMAP:
    def __init__(self, msgs):
        self.msgs = msgs

    def __call__(self, host):
        return self

    def login(self, u, p):
        pass

    def select(self, box, readonly=True):
        return "OK", [b"1"]

    def uid(self, cmd, *args):
        if cmd == "search":
            crit = args[1]
            hits = [str(i + 1).encode() for i, m in enumerate(self.msgs) if m["_filter"] in crit]
            return "OK", [b" ".join(hits)]
        out = []
        for uid in (args[0].split(b",") if isinstance(args[0], bytes) else [args[0]]):
            out += [(b"%d (UID %d BODY[] {n})" % (int(uid), int(uid)), self.msgs[int(uid) - 1]["raw"]), b")"]
        return "OK", out

    def logout(self):
        pass


def _mail(frm, subject, html, mid):
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = frm, subject, mid
    m["Date"] = "Mon, 28 Sep 2026 14:05:00 +0530"
    m.set_content("Please view this email in an HTML client.")
    m.add_alternative(html, subtype="html")
    return m.as_bytes()


def test_gmail_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GMAIL_USER", "me@gmail.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "x")
    monkeypatch.setattr(config, "GMAIL_SENDER_FILTERS", ["hdfcbank", "sbicard"])
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    db.init(tmp_path / "g.db")
    msgs = [
        {"_filter": "hdfcbank", "raw": _mail(
            "HDFC Bank InstaAlerts <alerts@hdfcbank.bank.in>", "You have done a UPI txn. Check details!",
            "<p>Dear Customer,</p><p>Rs.250.00 has been debited from account **1234 to VPA swiggy@icici SWIGGY on "
            "28-09-26. Your UPI transaction reference number is 426712345678.</p>", "<a@hdfc>")},
        {"_filter": "sbicard", "raw": _mail(
            "SBI Card <onlinesbicard@sbicard.com>", "Transaction Alert from SBI Card",
            "<table><tr><td>Rs.2,499.00 was spent on your SBI Credit Card ending 4321 at MYNTRA on 28/09/26.</td></tr></table>",
            "<b@sbi>")},
    ]
    monkeypatch.setattr(gmail_sync.imaplib, "IMAP4_SSL", FakeIMAP(msgs))
    r = gmail_sync.sync()
    assert r["ok"] and r["parsed"] == 2, r
    assert gmail_sync.sync()["exists"] == 2  # idempotent
    with db.connect() as c:
        rows = c.execute("SELECT merchant, amount, category, txn_time FROM transactions ORDER BY amount").fetchall()
    assert [tuple(r) for r in rows] == [("Swiggy", 250.0, "Food & Dining", "2026-09-28 14:05:00"),
                                        ("Myntra", 2499.0, "Shopping", "2026-09-28 14:05:00")]
