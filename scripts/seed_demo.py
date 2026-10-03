"""Seed realistic demo data through the real ingestion pipeline.

Usage: EM_DATA_DIR=/tmp/em-demo .venv/bin/python scripts/seed_demo.py
(Use a separate EM_DATA_DIR so demo data never mixes with your real data.)
"""

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expense_manager import config, db, ingest  # noqa: E402

random.seed(7)

UPI = [("SWIGGY", "swiggy@icici", 180, 650), ("ZOMATO", "zomato@hdfcbank", 200, 700), ("BLINKIT", "blinkit@axl", 150, 900),
       ("ZEPTO", "zepto@ybl", 120, 600), ("UBER INDIA", "uber@axisbank", 90, 450), ("RAPIDO", "rapido@ybl", 40, 180),
       ("CHAAYOS", "chaayos@paytm", 90, 300), ("STARBUCKS", "starbucks@hdfcbank", 300, 700),
       ("APOLLO PHARMACY", "apollo@icici", 150, 1200), ("RAMESH KUMAR", "q837261@ybl", 50, 400)]
CARD = [("AMAZON", 300, 4000), ("FLIPKART", 400, 3500), ("MYNTRA", 800, 3000), ("INDIAN OIL", 1500, 3000),
        ("BOOKMYSHOW", 400, 1200), ("DMART", 800, 3500)]


def hdfc_upi(amt, name, vpa, d, ref):
    return "AX-HDFCBK", f"Sent Rs.{amt:.2f}\nFrom HDFC Bank A/C *4821\nTo {name}\nOn {d:%d/%m/%y}\nRef {ref}\nNot You?\nCall 18002586161"


def axis_upi(amt, name, vpa, d, ref):
    return "JM-AXISBK", f"INR {amt:.2f} debited\nA/c no. XX7710\n{d:%d-%m-%y, %H:%M:%S}\nUPI/P2M/{ref}/{name}\nNot you? SMS BLOCKUPI Cust ID to 919951860002\nAxis Bank"


def icici_upi(amt, name, vpa, d, ref):
    return "VM-ICICIB", f"ICICI Bank Acct XX553 debited for Rs {amt:,.2f} on {d:%d-%b-%y}; {name} credited. UPI:{ref}. Call 18002662 for dispute."


def sbi_card(amt, name, d):
    return "BP-SBICRD", f"Rs.{amt:,.2f} spent on your SBI Credit Card ending 9034 at {name} on {d:%d/%m/%y}. Trxn. not done by you? Report at https://sbicard.com/Dispute"


def main():
    db.init()
    print("DB:", config.DB_PATH)
    today = datetime.now()
    start = (today.replace(day=1) - timedelta(days=150)).replace(day=1, hour=0, minute=0)
    ref = 426700000000
    n = 0
    with db.connect() as conn:
        def put(sender, body, when, source="sms"):
            nonlocal n
            ingest.ingest_message(conn, source=source, sender=sender, body=body, received_at=when, use_llm=False)
            n += 1

        d = start
        while d <= today:
            for _ in range(random.randint(1, 4)):
                when = d.replace(hour=random.randint(8, 22), minute=random.randint(0, 59))
                if when > today:
                    continue
                ref += random.randint(1, 999)
                name, vpa, lo, hi = random.choice(UPI)
                amt = round(random.uniform(lo, hi), 0)
                put(*random.choice([hdfc_upi, axis_upi, icici_upi])(amt, name, vpa, when, ref), when)
                if random.random() < 0.3:  # the same txn also arrives by email -> should merge
                    put("alerts@hdfcbank.bank.in", f"Dear Customer, Rs.{amt:.2f} has been debited from account **4821 to VPA {vpa} {name} on {when:%d-%m-%y}. Your UPI transaction reference number is {ref}.", when + timedelta(minutes=2), "email")
            if random.random() < 0.45:
                when = d.replace(hour=random.randint(10, 21), minute=random.randint(0, 59))
                if when <= today:
                    name, lo, hi = random.choice(CARD)
                    put(*sbi_card(round(random.uniform(lo, hi), 0), name, when), when)
            if d.day == 1:
                w = d.replace(hour=9)
                put("AD-AXISBK", f"INR 1,85,000.00 credited\nA/c no. XX7710\n{w:%d-%m-%y, %H:%M:%S} IST\nNEFT/ACME TECHNOLOGIES PVT LTD\n- Axis Bank", w)
            if d.day == 3:
                w = d.replace(hour=10)
                put("VM-HDFCBK", f"Rs.35000.00 debited from a/c **4821 on {w:%d-%m-%y} to VPA landlord.sharma@okhdfcbank(UPI Ref No {ref + 7}).", w)
                put(*sbi_card(649, "NETFLIX", w), w + timedelta(minutes=5))
                put(*sbi_card(119, "SPOTIFY", w), w + timedelta(minutes=6))
            if d.day == 5:
                w = d.replace(hour=11)
                put("JM-AXISBK", f"INR 15,000.00 debited\nA/c no. XX7710\n{w:%d-%m-%y, %H:%M:%S}\nUPI/P2M/{ref + 9}/GROWW\nAxis Bank", w)
                put("JM-AXISBK", f"INR 40,000.00 debited\nA/c no. XX7710\n{w:%d-%m-%y, %H:%M:%S}\nUPI/P2A/{ref + 11}/SELF\nAxis Bank", w + timedelta(minutes=10))
                put("AX-HDFCBK", f"Rs.40000.00 credited to HDFC Bank A/c XX4821 on {w:%d-%m-%y} by a/c linked to VPA me@axl (UPI Ref No {ref + 11}).", w + timedelta(minutes=11))
            if d.day == 18:
                w = d.replace(hour=20)
                put("VM-HDFCBK", f"Rs.18500.00 debited from a/c **4821 on {w:%d-%m-%y} to VPA cred.club@axisb(UPI Ref No {ref + 13}).", w)
                put("BP-SBICRD", f"We have received payment of Rs.18,500.00 towards your SBI Card XX9034 on {w:%d-%b-%y}. Thank you.", w + timedelta(minutes=20))
            if d.day == 12:
                w = d.replace(hour=9)
                put("AX-HDFCBK", f"Rs.2000.00 withdrawn at ATM from HDFC Bank A/c XX4821 on {w:%d-%m-%y}. Avl Bal Rs 45,000.00", w)
            d += timedelta(days=1)
        # a couple of noise messages
        put("AX-HDFCBK", "482913 is OTP for txn of Rs 999.00 at AMAZON on HDFC Bank card. Do not share.", today)
        put("VM-ICICIB", "Dear customer, Rs 250 transferred to Acct via mode IMPS-P2A check details in app", today)
        for cat, lim in {"Food & Dining": 12000, "Groceries": 10000, "Shopping": 8000, "Travel": 5000, "Entertainment": 1500}.items():
            conn.execute("INSERT OR REPLACE INTO budgets VALUES(?, ?)", (cat, lim))
    print(f"ingested {n} messages")


if __name__ == "__main__":
    main()
