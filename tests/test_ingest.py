from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from expense_manager import config, db, ingest


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    db.init(tmp_path / "t.db")
    with db.connect() as c:
        yield c


def T(h, m=0):
    return datetime(2026, 9, 28, h, m)


def test_sms_and_email_for_same_txn_are_merged(conn):
    r1 = ingest.ingest_message(conn, source="sms", sender="AX-HDFCBK", received_at=T(14, 0),
                               body="Sent Rs.250.00\nFrom HDFC Bank A/C *1234\nTo SWIGGY\nOn 28/09/26\nRef 426712345678")
    r2 = ingest.ingest_message(
        conn, source="email", sender="alerts@hdfcbank.bank.in", received_at=T(14, 3),
        body="Rs.250.00 has been debited from account **1234 to VPA swiggy.stores@icici SWIGGY on 28-09-26. "
             "Your UPI transaction reference number is 426712345678.")
    assert r1["status"] == "parsed" and r2["status"] == "duplicate"
    t = conn.execute("SELECT * FROM transactions").fetchall()
    assert len(t) == 1
    assert t[0]["sources"] == "email,sms"
    assert t[0]["payee_vpa"] == "swiggy.stores@icici"
    assert t[0]["category"] == "Food & Dining"


def test_same_sms_twice_is_not_double_counted_but_two_real_purchases_are(conn):
    body = "Rs.20.00 spent on your SBI Credit Card ending 4321 at CHAI POINT on 28/09/26."
    assert ingest.ingest_message(conn, source="sms", sender="SBICRD", body=body, received_at=T(10))["status"] == "parsed"
    assert ingest.ingest_message(conn, source="sms", sender="SBICRD", body=body, received_at=T(10))["status"] == "exists"
    assert ingest.ingest_message(conn, source="sms", sender="SBICRD", body=body, received_at=T(10, 5))["status"] == "parsed"
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2


def test_self_transfer_detected(conn):
    ingest.ingest_message(conn, source="sms", sender="VM-HDFCBK", received_at=T(9),
                          body="Rs.10000.00 debited from a/c **1234 on 28-09-26 to VPA ravi@okicici(UPI Ref No 426700000001).")
    ingest.ingest_message(conn, source="sms", sender="VM-ICICIB", received_at=T(9, 1),
                          body="Dear Customer, Acct XX901 is credited with Rs 10,000.00 on 28-Sep-26 from RAVI. UPI:426700000001-ICICI Bank.")
    cats = [r[0] for r in conn.execute("SELECT category FROM transactions")]
    assert cats == ["Self Transfer", "Self Transfer"]


def test_learned_rule_applies_to_past_and_future(conn):
    b = "Rs.{a}.00 debited from a/c **1234 on 28-09-26 to VPA q12345@ybl(UPI Ref No 4267000000{n})."
    ingest.ingest_message(conn, source="sms", sender="VM-HDFCBK", body=b.format(a=40, n=11), received_at=T(8))
    assert conn.execute("SELECT category FROM transactions").fetchone()[0] == "Other"
    assert ingest.add_rule(conn, "q12345@ybl", "Groceries") == 1
    ingest.ingest_message(conn, source="sms", sender="VM-HDFCBK", body=b.format(a=60, n=12), received_at=T(18))
    assert [r[0] for r in conn.execute("SELECT category FROM transactions")] == ["Groceries", "Groceries"]


def test_api_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "api.db")
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    monkeypatch.setattr(config, "GMAIL_USER", "")
    from expense_manager import api

    with TestClient(api.app) as client:
        # webhook requires the token
        payload = {"from": "BP-SBICRD", "text": "Rs.1,250.00 spent on your SBI Credit Card ending 4321 at AMAZON on 28/09/26.",
                   "receivedStamp": int(T(12).timestamp() * 1000)}
        assert client.post("/api/ingest/sms", json=payload).status_code == 401
        r = client.post(f"/api/ingest/sms?token={api.TOKEN}", json=payload).json()
        assert r["status"] == "parsed" and r["transaction"]["category"] == "Shopping"

        client.post(f"/api/ingest/sms?token={api.TOKEN}", json={
            "from": "BP-SBICRD", "receivedStamp": int(T(13).timestamp() * 1000),
            "text": "We have received payment of Rs.25,000.00 towards your SBI Card XX4321 on 28-Sep-26."})
        client.post("/api/transactions", json={"amount": 100, "merchant": "Chai", "txn_time": "2026-09-27T08:00"})

        s = client.get("/api/summary?month=2026-09").json()
        assert s["total_spend"] == 1350.0          # card payment credit is not counted
        assert {c["category"] for c in s["by_category"]} == {"Shopping", "Other"}
        assert sum(d["amount"] for d in s["daily"]) == 1350.0

        tid = r["transaction_id"]
        p = client.patch(f"/api/transactions/{tid}", json={"category": "Gifts & Donations", "apply_to_similar": True}).json()
        assert p["category"] == "Gifts & Donations" and p["category_source"] == "user"
        assert client.get("/api/rules").json()[0]["pattern"] == "amazon"

        assert len(client.get("/api/trend?months=3").json()) == 3
        assert client.get("/api/insights?month=2026-09").status_code == 200
        assert "AMAZON" in client.get("/api/export.csv?month=2026-09").text.upper()

        u = client.post("/api/ingest/text", json={"text": "Your a/c balance is Rs 5,000", "source": "manual"}).json()
        assert u["status"] == "ignored"
        st = client.get("/api/status").json()
        assert st["transactions"] == 3 and st["token"] == api.TOKEN


def test_llm_fallback_for_unparsed(conn, monkeypatch):
    from expense_manager import llm
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    calls = []

    def fake_chat(system, user, timeout=60):
        calls.append(system[:20])
        if "extract" in system:
            return {"is_transaction": True, "amount": "250", "direction": "debit", "merchant": "RAMESH TEA STALL",
                    "account_last4": "553", "instrument": "NETBANKING", "ref_no": None, "date": "2026-09-28", "time": None}
        return {"category": "Food & Dining"}

    monkeypatch.setattr(llm, "_chat", fake_chat)
    r = ingest.ingest_message(conn, source="sms", sender="VM-ICICIB", received_at=T(15),
                              body="Dear customer, Rs 250 transferred to Acct via mode IMPS-P2A check details in app")
    assert r["status"] == "parsed" and r["parsed_by"] == "llm"
    t = r["transaction"]
    assert (t["merchant"], t["category"], t["category_source"], t["txn_time"]) == (
        "Ramesh Tea Stall", "Food & Dining", "llm", "2026-09-28 15:00:00")
    assert len(calls) == 2


SMS_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<smses count="4">
  <sms protocol="0" address="JM-AXISBK-S" date="{t1}" type="1" body="INR 349.00 debited&#10;A/c no. XX5678&#10;28-09-26, 13:22:10&#10;UPI/P2M/426700001111/ZOMATO LTD&#10;Axis Bank" />
  <sms protocol="0" address="AX-HDFCBK" date="{t2}" type="1" body="Sent Rs.250.00&#10;From HDFC Bank A/C *1234&#10;To SWIGGY&#10;On 28/09/26&#10;Ref 426712345678" />
  <sms protocol="0" address="+919876543210" date="{t2}" type="1" body="sent you Rs 500" />
  <sms protocol="0" address="AX-HDFCBK" date="{t2}" type="2" body="outgoing message" />
</smses>"""


def test_sms_backup_import_via_api(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "s.db")
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    monkeypatch.setattr(config, "GMAIL_USER", "")
    from expense_manager import api
    xml = SMS_XML.format(t1=int(T(13, 22).timestamp() * 1000), t2=int(T(14).timestamp() * 1000))
    with TestClient(api.app) as client:
        r = client.post("/api/import/sms-backup", content=xml.encode(), headers={"Content-Type": "application/xml"}).json()
        assert r["ok"] and r["total_sms"] == 4 and r["bank_sms"] == 2 and r["parsed"] == 2, r
        again = client.post("/api/import/sms-backup", content=xml.encode()).json()
        assert again["exists"] == 2 and again.get("parsed", 0) == 0
        assert client.post("/api/import/sms-backup", content=b"not xml").json()["ok"] is False
        assert client.get("/api/status").json()["transactions"] == 2


def test_sms_webhook_payload_variants(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "w.db")
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    monkeypatch.setattr(config, "GMAIL_USER", "")
    from expense_manager import api
    url = f"/api/ingest/sms?token={api.TOKEN}"
    with TestClient(api.app) as client:
        # Empty body (forwarder with no JSON template) must be rejected so the app retries, not dropped.
        r = client.post(url, content=b"", headers={"Content-Type": "application/json"})
        assert r.status_code == 400 and "template" in r.json()["detail"]
        assert client.get("/api/status").json()["sms"]["last_heartbeat"]
        # Unescaped newlines inside the JSON string (multi-line bank SMS) are tolerated.
        raw = b'{"from":"JM-AXISBK-S","text":"INR 349.00 debited\nA/c no. XX5678\n28-09-26, 13:22:10\nUPI/P2M/426700001111/ZOMATO LTD"}'
        r = client.post(url, content=raw, headers={"Content-Type": "application/json"}).json()
        assert r["status"] == "parsed" and r["transaction"]["merchant"] == "Zomato"


def test_tailscale_identity_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "a.db")
    monkeypatch.setattr(config, "GMAIL_USER", "")
    from expense_manager import api, tailscale
    monkeypatch.setattr(tailscale, "owner_logins", lambda: {"me@github"})
    with TestClient(api.app) as client:
        assert client.get("/api/categories").status_code == 200                                   # local browser
        assert client.get("/api/categories", headers={"Tailscale-User-Login": "Me@GitHub"}).status_code == 200
        assert client.get("/api/categories", headers={"Tailscale-User-Login": "eve@github"}).status_code == 401
        assert client.get(f"/api/categories?token={api.TOKEN}",
                          headers={"Tailscale-User-Login": "eve@github"}).status_code == 200
        # The SMS webhook always needs the token, even from a trusted identity.
        r = client.post("/api/ingest/sms", json={"text": "x"}, headers={"Tailscale-User-Login": "me@github"})
        assert r.status_code == 401
