# ₹ Expense Manager

A self-hosted, mostly automatic expense tracker for Indian bank, credit card and UPI alerts. It reads
your **bank SMS** (forwarded from your Android phone) and your **Gmail alerts**, parses them into
transactions, categorises them, and shows your spending on a dashboard that also works on your phone.

Tuned for **HDFC, Axis and ICICI** (UPI and net banking) and **SBI Card**, with generic fallbacks that
work for most other Indian banks.

```
 Android phone ──(SMS to URL Forwarder → Tailscale HTTPS)──┐
                                                           ▼
 Gmail ──(IMAP poll every 15 min)──────────────▶ FastAPI on your Mac ──▶ SQLite (~/.expense-manager)
                                                           │
                          regex parser ─▶ dedupe ─▶ categorise (rules → keywords → optional LLM)
                                                           │
             Dashboard: http://localhost:8642 on the Mac · https://<mac>.<tailnet>.ts.net:8443 on the phone
```

## Features

- **Automatic ingestion.** SMS arrive by webhook in real time, and Gmail is polled every 15 minutes
  (with historical backfill). Old SMS can be imported from an *SMS Backup & Restore* file.
- **Deterministic parsing first.** Regex handles the formats of HDFC, Axis, ICICI and SBI Card (SMS and
  email). It ignores:
  - OTPs (but not the "never share your OTP" safety text in real alerts)
  - Due-date reminders and statements
  - Mandate setups and payment requests
  - Loan applications and promotional emails
  - Declined or failed transactions
  - SMS from non-bank senders
- **Merchant clean-up.**
  - Gateway prefixes are stripped (`RAZSwiggy`, `PYU…`, `PTM…`).
  - Bank reference paths (`NEFT/…/NAME`) are reduced to the payee name.
  - Brand spellings are merged ("Swiggypvtltdfood2" → Swiggy, "AMAZON PAY IN GROCERY" → Amazon Fresh).
- **Foreign-currency charges** (for example "USD 236.00") are converted to ₹ at approximate rates. The
  original amount is kept in the transaction's notes.
- **Optional LLM fallback.** It runs only for messages the rules couldn't parse and for merchants no
  rule matches. Use Ollama (fully local) or any OpenAI-compatible API.
- **De-duplication.** The same transaction arriving by SMS *and* email is merged, matched on UPI
  reference or on amount + account + time.
- **No double counting**
  - **Credit card bill payments** (CRED, the SBI Card payment itself, and so on) are excluded from spend,
    because the card purchases were already counted.
  - **Self-transfers.** A debit from one of your accounts matched by a credit to another within 30
    minutes, or any payment to or from your own name (`EM_SELF_NAMES`), is a self-transfer.
  - **Investments** (Groww, Zerodha, SIPs) are tracked separately from spend.
  - **Loan EMIs** are counted once, labelled "Loans & EMI". The bank's "credited to your loan account"
    confirmation is ignored.
  - **Refunds** reduce spend in their category.
- **Learns from you.** Change a category and choose "all from this merchant"; that saves a rule which
  updates past transactions and categorises future ones.
- **Dashboard**
  - Month total with the change against the same period last month
  - Average per day and projected month-end spend
  - Daily spend with a cumulative line; tap a day to see its transactions
  - Category donut and 12-month trend
  - Budgets with pace-aware progress bars
  - Top merchants, and recurring payments and subscriptions detected automatically
  - Biggest spends, split by payment method and by account/card, and spend by weekday
- **Transactions page.** Daily list with day totals, search and filters, re-categorisation, notes,
  exclude/delete, manual entry, and CSV export. Tap a transaction to edit it and see the original alert.
- **Review queue.** Messages that couldn't be parsed land here. You can retry them (with AI), add them
  manually, or dismiss them. You can also paste any SMS or email to parse it.
- **Works on your phone.**
  - Layout built for phone screens, with a bottom tab bar, and Android's Back button moves between tabs.
  - Can be installed as an app from Chrome.
  - QR codes in Settings for the phone dashboard and the forwarder URL.

## Setup

```bash
./run.sh                          # first run: creates .venv, installs deps, copies .env.example -> .env
open http://localhost:8642
```

Configure Gmail and SMS (below). Then install the background service (step 5) so it keeps running.

### 1. `.env` settings

| Variable | What it does |
|---|---|
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Gmail account and App Password (see step 2) |
| `GMAIL_BACKFILL_DAYS` | How far back the very first Gmail sync goes (default 90) |
| `GMAIL_POLL_MINUTES` | Gmail polling interval (default 15) |
| `GMAIL_SENDER_FILTERS` | Substrings of the From: address to read (default: HDFC, Axis, ICICI, SBI Card) |
| `EM_SELF_NAMES` | Your name as banks print it, e.g. `Ravi Kumar`. Payments to and from it, including truncated forms like "RAVI KUM", are self-transfers |
| `FX_RATES` | Override the approximate rates for foreign-currency alerts, e.g. `USD=88,EUR=103` |
| `EM_PORT` | Server port (default 8642) |
| `EM_TOKEN` | Shared secret for the SMS webhook and non-Tailscale access (auto-generated in `~/.expense-manager/token` if empty) |
| `EM_DATA_DIR` | Where the database lives (default `~/.expense-manager`) |
| `EM_TAILSCALE_USERS` | Tailscale logins allowed into the dashboard without the token (default: this Mac's Tailscale owner) |
| `LLM_*` | Optional LLM fallback (step 4) |

### 2. Gmail

1. Turn on 2-Step Verification on your Google account.
2. Create an App Password at <https://myaccount.google.com/apppasswords>.
3. Put `GMAIL_USER` and `GMAIL_APP_PASSWORD` in `.env` and restart (or redeploy, step 5).
4. The first sync imports `GMAIL_BACKFILL_DAYS` of history. To go further back, use **Settings → Backfill
   days**.

Only mail whose From address matches `GMAIL_SENDER_FILTERS` is read. The mailbox is opened read-only, and
emails are fetched in batches of 50.

### 3. Phone access and SMS forwarding (Tailscale)

Install [Tailscale](https://tailscale.com) on the Mac and the phone (same account), then run this once on
the Mac:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8642
```

The app is then at `https://<mac>.<tailnet>.ts.net:8443/`.

- It has a valid HTTPS certificate and is reachable only from your tailnet. It works on mobile data too.
- The `serve` setting survives reboots. To remove it: `tailscale serve --https=8443 off`.
- It uses its own port, not a sub-path, on purpose. Another web app installed on the phone with scope `/` on
  the same hostname would otherwise capture it.

**Dashboard on the phone.**
1. Scan the **Phone dashboard** QR code in Settings (shown only on the Mac) and open it in Chrome.
2. Tap ⋮ → **Install app** / **Add to Home screen**.

If it opens inside another installed web app from the same Mac, see Troubleshooting.

**SMS forwarding**

1. Install **SMS to URL Forwarder**
   ([GitHub](https://github.com/bogkonstantin/android_income_sms_gateway_webhook), also on F-Droid). Any
   app that can POST SMS to a URL will work. On Android 13 and later, sideloaded apps may first need **App
   info → ⋮ → Allow restricted settings** before you can grant the SMS permission.
2. Add a rule:
   - **Sender:** `*`. The server drops anything that isn't from a bank sender ID such as `AX-HDFCBK`.
   - **URL:** scan the **Forwarder URL** QR code in Settings, or copy it:
     `https://<mac>.<tailnet>.ts.net:8443/api/ingest/sms?token=<your-token>`
   - **JSON template:** `{"from":"%from%","text":"%text%","sentStamp":%sentStamp%,"receivedStamp":%receivedStamp%,"sim":"%sim%"}`
3. Set the app's battery usage to **Unrestricted**. In the Tailscale app, enable Android's **Always-on VPN**.
4. Check **Settings → SMS**: *Last SMS received* updates when a bank SMS arrives. *Forwarder last seen* also
   updates on the app's test or heartbeat requests.

Without Tailscale, use `http://<mac-lan-ip>:8642/api/ingest/sms?token=…`. That only works on the same Wi-Fi.
The webhook also accepts form-encoded bodies, the keys `message`, `body`, `sender` and `address`, and JSON
with raw newlines in the SMS text.

**Old SMS.** On the phone, open *SMS Backup & Restore* → Back up → Messages only → Local backup. Copy the
`sms-*.xml` file to the Mac, then upload it in **Settings → Import old SMS** (or run
`.venv/bin/python scripts/import_sms_backup.py sms-*.xml`). Only bank SMS are imported. Transactions already
captured from Gmail are merged, and re-importing the same file adds nothing new.

### 4. Optional LLM fallback

```
LLM_ENABLED=true
# Local and private:  brew install ollama && ollama pull llama3.1
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3.1
# Or OpenAI:  LLM_BASE_URL=https://api.openai.com/v1  LLM_MODEL=gpt-4o-mini  LLM_API_KEY=sk-...
```

Parsed-by-AI transactions show an **AI** chip in the list. **Settings → Re-categorise "Other"** runs
rules, keywords and the LLM over anything still uncategorised.

### 5. Run in the background (start at login, restart on crash)

```bash
scripts/install_autostart.sh            # install, or redeploy after changing code / .env
scripts/install_autostart.sh uninstall
```

macOS doesn't let background services read OneDrive / `~/Library/CloudStorage` folders. The script
therefore copies the app (code + `.env`) to `~/.expense-manager/app` and runs it from there as a LaunchAgent.

- **Re-run the script after editing code or `.env`** to redeploy.
- Your data in `~/.expense-manager/expenses.db` is shared and not touched.
- Logs go to `~/.expense-manager/server.log`.
- While the service is running, don't also start `./run.sh` on the same port; both use 8642.

## Security and privacy

- All data stays on your Mac in `~/.expense-manager/expenses.db`, outside OneDrive on purpose, because
  cloud sync can corrupt live SQLite files.
- **The SMS webhook always requires the token.**
- **Dashboard access**
  - No token needed on `localhost`.
  - Through Tailscale (`:8443`), your own Tailscale login is trusted using the identity header that
    `tailscale serve` adds. Other logins need the token.
  - Over the LAN IP, the token is needed. Open `http://<mac-ip>:8642/?token=<token>` once and the browser
    remembers it.
- The server runs with `--no-proxy-headers`, so a forwarded IP header can't make a remote request look local.
- The token and the QR codes are only shown to trusted viewers (the Mac itself or your Tailscale login).
- With the LLM off, nothing leaves your machine. With Ollama, nothing leaves either. With OpenAI,
  only unparsed messages and uncategorised merchant names are sent.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Forwarder shows `ERROR_RETRY` for its **test** or **heartbeat** | Expected: those send an empty body, which the server rejects (`400`). Real SMS still work; check *Last SMS received* in Settings |
| Every SMS fails with `401` | Token typo in the URL (e.g. `tokenn=`). Re-scan the Forwarder URL QR code |
| Every real SMS fails with `400` | The JSON template is empty or uses other field names. Set the template from step 3 |
| Phone opens Expenses inside another installed web app (e.g. a Todo app on the same Mac) | Android matches installed web apps by hostname. Go to **Settings → Apps → that app → Open by default** and turn off *Open supported links*, then open the URL in Chrome and install Expenses |
| Dashboard doesn't show a new feature | Reload; static files are served with `Cache-Control: no-cache` |
| Service log shows `Operation not permitted` | It's running from OneDrive. Re-run `scripts/install_autostart.sh`, which deploys to `~/.expense-manager/app` |
| `address already in use` on 8642 | The background service is already running. Use it, or `scripts/install_autostart.sh uninstall` first |
| A bank alert lands in **Review** | Its format isn't recognised yet. Add the text as a test in `tests/test_parser.py` and adjust `parser.py` |

## Development

```bash
.venv/bin/python -m pytest -q                                          # parser, ingestion, API, Gmail tests
EM_DATA_DIR=/tmp/em-demo .venv/bin/python scripts/seed_demo.py         # 5 months of fake data
EM_DATA_DIR=/tmp/em-demo EM_PORT=8799 ./run.sh                         # demo instance next to the real one
scripts/install_autostart.sh                                           # deploy your changes to the service
```

Tests use a temporary data directory and never touch your real database.

| Path | What |
|---|---|
| `expense_manager/parser.py` | Regex parser for bank, card and UPI alerts, merchant clean-up, brand names, currencies |
| `expense_manager/categorizer.py` | Categories, keyword map, self-name detection, and the non-spend categories |
| `expense_manager/ingest.py` | Pipeline: store raw message → parse → LLM fallback → dedupe → categorise → self-transfer detection |
| `expense_manager/gmail_sync.py` | IMAP fetcher (batched) |
| `expense_manager/sms_backup.py` | *SMS Backup & Restore* XML importer |
| `expense_manager/tailscale.py` | Tailscale CLI helpers (IP, owner login, `serve` URL) |
| `expense_manager/llm.py` | OpenAI-compatible LLM client |
| `expense_manager/api.py` | FastAPI app: webhook, dashboard APIs, auth, Gmail poller |
| `expense_manager/static/` | Dashboard (vanilla JS + Chart.js), PWA manifest and icons |
| `scripts/` | Background-service installer, SMS backup import, demo-data seeder |
