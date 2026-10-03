from datetime import datetime

import pytest

from expense_manager.parser import parse

RECV = datetime(2026, 9, 28, 14, 30, 0)

CASES = [
    # (sender, text, expected subset)
    ("AX-HDFCBK",
     "Sent Rs.250.00\nFrom HDFC Bank A/C *1234\nTo SWIGGY\nOn 28/09/26\nRef 426712345678\n"
     "Not You?\nCall 18002586161/SMS BLOCK UPI to 7308080808",
     dict(amount=250.0, direction="debit", merchant="Swiggy", bank="HDFC", account_last4="1234",
          instrument="UPI", ref_no="426712345678")),
    ("VM-HDFCBK",
     "Rs.500.00 debited from a/c **1234 on 28-09-26 to VPA rahul.k@okaxis(UPI Ref No 426712345699). "
     "Not you? Call on 18002586161 to report",
     dict(amount=500.0, direction="debit", payee_vpa="rahul.k@okaxis", merchant="Rahul K",
          account_last4="1234", ref_no="426712345699", instrument="UPI")),
    ("JD-HDFCBK",
     "Rs.1000.00 credited to HDFC Bank A/c XX1234 on 28-09-26 by a/c linked to VPA priya99@ybl "
     "(UPI Ref No 426712340000).",
     dict(amount=1000.0, direction="credit", payee_vpa="priya99@ybl", merchant="Priya99",
          account_last4="1234")),
    ("JM-AXISBK-S",
     "INR 349.00 debited\nA/c no. XX5678\n28-09-26, 13:22:10\nUPI/P2M/426700001111/ZOMATO LTD\n"
     "Not you? SMS BLOCKUPI Cust ID to 919951860002\nAxis Bank",
     dict(amount=349.0, direction="debit", merchant="Zomato", bank="Axis", account_last4="5678",
          ref_no="426700001111", instrument="UPI", txn_time=datetime(2026, 9, 28, 13, 22, 10))),
    ("AD-AXISBK",
     "INR 2,000.00 credited\nA/c no. XX5678\n28-09-26, 09:01:00 IST\nUPI/P2A/426700002222/AMIT SHARMA\n- Axis Bank",
     dict(amount=2000.0, direction="credit", merchant="Amit Sharma", account_last4="5678")),
    ("VM-ICICIB",
     "ICICI Bank Acct XX901 debited for Rs 1,234.50 on 28-Sep-26; BLINKIT credited. UPI:426711112222. "
     "Call 18002662 for dispute. SMS BLOCK 901 to 9215676766.",
     dict(amount=1234.5, direction="debit", merchant="Blinkit", bank="ICICI", account_last4="901",
          ref_no="426711112222", instrument="UPI")),
    ("VK-ICICIT",
     "Dear Customer, Acct XX901 is credited with Rs 15,000.00 on 28-Sep-26 from ROHAN MEHTA. "
     "UPI:426733334444-ICICI Bank.",
     dict(amount=15000.0, direction="credit", merchant="Rohan Mehta", bank="ICICI")),
    ("VM-ICICIB",
     "ICICI Bank Acct XX901 debited with Rs 5,000.00 on 28-Sep-26 & Acct XX456 credited. IMPS:426755556666. "
     "Call 18002662 for dispute or SMS BLOCK 901 to 9215676766",
     dict(amount=5000.0, direction="debit", instrument="NETBANKING", ref_no="426755556666")),
    ("BP-SBICRD",
     "Rs.1,250.00 spent on your SBI Credit Card ending 4321 at AMAZON on 28/09/26. "
     "Trxn. not done by you? Report at https://sbicard.com/Dispute",
     dict(amount=1250.0, direction="debit", merchant="Amazon", bank="SBI Card", account_last4="4321",
          instrument="CREDIT_CARD")),
    ("AX-SBICRD",
     "Rs.180.00 spent on your SBI Credit Card ending with 4321 at UBER INDIA via UPI on 28-09-26 "
     "(Ref No. 426799990000).",
     dict(amount=180.0, merchant="Uber", instrument="CREDIT_CARD", ref_no="426799990000")),
    ("BP-SBICRD",
     "We have received payment of Rs.25,000.00 towards your SBI Card XX4321 on 28-Sep-26. "
     "Thank you. Avl limit: Rs 1,20,000.",
     dict(amount=25000.0, direction="credit", hint_category="Credit Card Payment", instrument="CREDIT_CARD")),
    ("VM-HDFCBK",
     "Rs.25000.00 debited from a/c **1234 on 28-09-26 to VPA cred.club@axisb(UPI Ref No 426788887777).",
     dict(amount=25000.0, direction="debit", hint_category="Credit Card Payment")),
    ("VM-HDFCBK",
     "Rs.2000.00 withdrawn at ATM from HDFC Bank A/c XX1234 on 28-09-26. Avl Bal Rs 45,000.00",
     dict(amount=2000.0, direction="debit", instrument="ATM", hint_category="Cash")),
    ("VM-ICICIB",
     "INR 799.00 spent using ICICI Bank Card XX7788 on 28-Sep-26 on NETFLIX. Avl Limit: INR 2,34,567.00. "
     "If not you, call 1800 2662/SMS BLOCK 7788 to 9215676766",
     dict(amount=799.0, merchant="Netflix", instrument="CREDIT_CARD", account_last4="7788")),
    ("JD-HDFCBK",
     "Refund of Rs.349.00 credited to HDFC Bank A/c XX1234 from ZOMATO on 28-09-26.",
     dict(amount=349.0, direction="credit", hint_category="Refund", merchant="Zomato")),
]


@pytest.mark.parametrize("sender,text,expected", CASES)
def test_parses_bank_alerts(sender, text, expected):
    res = parse(text, sender=sender, received_at=RECV)
    assert res.status == "parsed", res.reason
    got = res.txn
    for k, v in expected.items():
        assert getattr(got, k) == v, f"{k}: {getattr(got, k)!r} != {v!r}"


IGNORED = [
    ("AX-HDFCBK", "123456 is OTP for txn of Rs 500.00 at AMAZON on HDFC Bank card ending 1234. Do not share."),
    ("BP-SBICRD", "Your SBI Card statement has been generated. Total Amount Due Rs 12,345.00. Min Amount Due Rs 620. "
                  "Due date 15-Oct-26"),
    ("VM-ICICIB", "Rs 499 will be debited from your account on 01-Oct-26 towards NETFLIX mandate."),
    ("VM-ICICIB", "ROHIT has requested money of Rs 300 from you on Google Pay. On approving, amount will be debited."),
    ("JM-AXISBK", "Transaction of INR 500.00 on Axis Bank Card XX5678 at AMAZON has been declined due to insufficient funds."),
    ("VM-HDFCBK", "Get a pre-approved Personal Loan of up to Rs 5,00,000 credited instantly! Apply now"),
    ("+919876543210", "Hey, sent you Rs 500 for dinner"),
    ("VM-SWIGGY", "Your Swiggy order of Rs 250 has been delivered"),
]


@pytest.mark.parametrize("sender,text", IGNORED)
def test_ignores_non_transactions(sender, text):
    res = parse(text, sender=sender, received_at=RECV)
    assert res.status == "ignored", (res.status, res.txn)


def test_html_email_hdfc():
    body = """<html><body><table><tr><td>Dear Customer,</td></tr>
    <tr><td>Rs.250.00 has been debited from account **1234 to VPA swiggy.stores@icici SWIGGY on 28-09-26.
    Your UPI transaction reference number is 426712345678.</td></tr>
    <tr><td>Please call 18002586161 to report if this transaction was not authorized by you.</td></tr>
    </table><p>Offers: Get up to Rs 5,000 cashback on your next purchase</p></body></html>"""
    res = parse(body, sender="alerts@hdfcbank.bank.in", subject="❗  You have done a UPI txn. Check details!",
                received_at=RECV, source="email")
    assert res.status == "parsed", res.reason
    t = res.txn
    assert (t.amount, t.direction, t.merchant, t.payee_vpa, t.ref_no, t.bank) == (
        250.0, "debit", "Swiggy", "swiggy.stores@icici", "426712345678", "HDFC")


def test_email_sbi_card():
    body = ("Dear Cardholder, We would like to inform you that Rs.2,499.00 was spent on your SBI Credit Card "
            "ending 4321 at MYNTRA DESIGNS on 27/09/26. Available credit limit is Rs 1,10,000.00.")
    res = parse(body, sender="onlinesbicard@sbicard.com", subject="Transaction Alert from SBI Card",
                received_at=RECV, source="email")
    assert res.status == "parsed"
    t = res.txn
    assert (t.amount, t.merchant, t.instrument, t.txn_time) == (
        2499.0, "Myntra", "CREDIT_CARD", datetime(2026, 9, 27, 12, 0))


def test_date_only_same_day_uses_received_time():
    res = parse("Rs.100.00 spent on your SBI Credit Card ending 4321 at CAFE on 28/09/26.", sender="SBICRD",
                received_at=RECV)
    assert res.txn.txn_time == RECV


# ---- Formats seen in real Gmail alerts (numbers changed) -------------------------------------------------
SAFETY_TIP = ("Safe Banking Tip: Never share your Card Number, CVV, PIN, OTP, Internet Banking User ID, Password or "
              "URN with anyone. Exclusive offer! Now convert your purchases of Rs.200 and above into Flexipay EMIs.")
REAL_EMAILS = [
    ("SBI Card <onlinesbicard@sbicard.com>", "Transaction Alert from SBI Card",
     "Dear Cardholder,\nThis is to inform you that,\nRs.322.00 spent on your SBI Credit Card ending 1111 at "
     "RAZSwiggy on 26/09/26. Trxn. not done by you? Report at https://sbicard.com/Dispute .\n" + SAFETY_TIP,
     dict(amount=322.0, merchant="Swiggy", instrument="CREDIT_CARD", account_last4="1111")),
    ("SBI Card <onlinesbicard@sbicard.com>", "Transaction Alert from SBI Card",
     "Dear Cardholder,\nUSD2.12 spent on your SBI Credit Card ending 1111 at DeepSeek on 26/09/26.\n" + SAFETY_TIP,
     dict(amount=round(2.12 * 88, 2), currency="USD", orig_amount=2.12, merchant="DeepSeek")),
    ("credit_cards@icicibank.com", "Transaction alert for your ICICI Bank Credit Card",
     "Your ICICI Bank Credit Card XX2222 has been used for a transaction of USD 236.00 on Sep 28, 2026 at 10:46:43. "
     "Info: ANTHROPIC.\nThe Available Credit Limit on your card is INR 5,71,498.43 and Total Credit Limit is "
     "INR 6,47,000.00.\nIn case you require any further information, write to us at customer.care@icicibank.com",
     dict(amount=236 * 88.0, currency="USD", merchant="Anthropic", account_last4="2222")),
    ("credit_cards@icicibank.com", "Transaction alert for your ICICI Bank Credit Card",
     "Your ICICI Bank Credit Card XX2222 has been used for a transaction of INR 504.00 on Sep 28, 2026 at 08:13:46. "
     "Info: AMAZON PAY IN E COMMERCE.\nThe Available Credit Limit on your card is INR 6,20,757.23.",
     dict(amount=504.0, merchant="Amazon", txn_time=datetime(2026, 9, 28, 8, 13, 46))),
    ("customercare@icicibank.com", "Transaction alert for your ICICI Bank Net Banking",
     "You have made an online payment of INR 35000.00 towards GROWW INVEST TECH PRIVATE LIMITED from your Account "
     "XX333 on Sep 27, 2026 at 12:50 hours. The Transaction ID is 1100000001.\nNever share your OTP, URN, CVV.",
     dict(amount=35000.0, direction="debit", merchant="Groww", account_last4="333", ref_no="1100000001",
          txn_time=datetime(2026, 9, 27, 12, 50))),
    ("customernotification@icici.bank.in", "NEFT transaction through ICICI Bank iMobile.",
     "You have made an online NEFT payment of Rs. 30,000.00 towards RAVI KUMAR on Sep 27, 2026 at 07:20 p.m. "
     "from your ICICI Bank Savings Account XXXX3333.",
     dict(amount=30000.0, merchant="Ravi Kumar", instrument="NETBANKING", txn_time=datetime(2026, 9, 27, 19, 20))),
    ("Axis Bank Alerts <alerts@axis.bank.in>", "Debit transaction alert for Axis Bank A/c",
     "24-09-2026 Dear Customer, We wish to inform you that your A/c no. XX4444 has been debited with INR 7000.00 on "
     "24-09-2026 11:55:06 IST by NBSM/100000002/GROWW INVES. To check your available balance, please click here .",
     dict(amount=7000.0, merchant="Groww", account_last4="4444")),
    ("Axis Bank Alerts <alerts@axis.bank.in>", "Debit transaction alert for Axis Bank A/c",
     "11-09-2026 Dear Customer, We wish to inform you that your A/c no. XX4444 has been debited with INR 30000.00 on "
     "11-09-2026 21:02:35 IST by NEFT/IR/AXOIR10000000003/R. To check your available balance, please click here . "
     "Should you wish to reach us, please call 18001035577. Always open to help you.",
     dict(amount=30000.0, merchant="Bank Transfer")),
    ("Axis Bank Alerts <alerts@axis.bank.in>", "Debit transaction alert for Axis Bank A/c",
     "05-09-2026 Dear Customer, your A/c no. XX4444 has been debited with INR 25000.00 on 05-09-2026 07:39:42 IST by "
     "PPR001234567890_EMI_05-09-. To check your available balance, please click here .",
     dict(amount=25000.0, merchant="Loan EMI", hint_category="Loans & EMI")),
    ("alerts@axis.bank.in", "Credit notification from Axis Bank",
     "INR 10.00 has been credited to A/c no. XX4444 on 02-09-26 at 02:27:10 IST.\n"
     "Reference no. - IMPS/P2A/600000000004/NAVIFINS",
     dict(amount=10.0, direction="credit", merchant="Navi")),
    ("HDFC Bank InstaAlerts <alerts@hdfcbank.bank.in>", "❗  You have done a UPI txn. Check details!",
     "Rs.5000.00 is debited from your account ending 5555 towards VPA groww.brk@validhdfc (GROWW INVEST TECH PVT LTD) "
     "on 28-09-26.\nUPI transaction reference no.: 600000000005.",
     dict(amount=5000.0, merchant="Groww", payee_vpa="groww.brk@validhdfc", ref_no="600000000005")),
]


@pytest.mark.parametrize("sender,subject,body,expected", REAL_EMAILS)
def test_real_email_formats(sender, subject, body, expected):
    res = parse(body, sender=sender, subject=subject, received_at=RECV, source="email")
    assert res.status == "parsed", res.reason
    for k, v in expected.items():
        got = getattr(res.txn, k)
        assert (round(got, 2) if isinstance(got, float) else got) == (round(v, 2) if isinstance(v, float) else v), \
            f"{k}: {got!r} != {v!r}"


IGNORED_EMAILS = [
    ("Axis Bank Alerts <alerts@axis.bank.in>", "Payment credited to your Loan account no. XX9090",
     "Payment of INR 25000 has been credited to your Loan account no. XX9090 on 05-09-2026."),
    ("customercare@icicibank.com", "ICICI Bank Flexicash Account",
     "Thank you for applying for the ICICI Bank Flexicash. Account xxxx3333. Overdraft Amount : Rs 500000. "
     "The final document will be sent to you post digital signature."),
    ("Axis Bank Alerts <alerts@axis.bank.in>", "Your payment of INR 30000.00 was unsuccessful",
     "Your payment request could not be processed. Payment amount: INR 30000.00 Reference number: ZZZZ0000TEST"),
    ("ICICI Bank <services@custcomm.icici.bank.in>", "Need funds? Try our Personal Loan",
     "Get a loan of Rs 5,00,000 credited to your account in minutes."),
    ("onlinesbicard@sbicard.com", "One Time Password (OTP) for your online transaction at SWIGGY",
     "The One Time Password (OTP) for your transaction at SWIGGY of Rs 322 with your SBI Credit Card ending 1111 is 952261."),
]


@pytest.mark.parametrize("sender,subject,body", IGNORED_EMAILS)
def test_real_emails_ignored(sender, subject, body):
    res = parse(body, sender=sender, subject=subject, received_at=RECV, source="email")
    assert res.status == "ignored", (res.status, res.txn)


def test_self_names(monkeypatch):
    from expense_manager import categorizer, config
    monkeypatch.setattr(config, "SELF_NAMES", ["ravi kumar"])
    assert all(categorizer.is_self(n) for n in ["Ravi Kumar", "RAVI KUM", "Ravi", "Mr Ravi Kumar"])
    assert not any(categorizer.is_self(n) for n in ["Ravi Sharma", "Ra", "Swiggy", None])
