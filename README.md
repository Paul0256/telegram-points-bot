# Telegram Points Bots + Google Sheets

Two Telegram bots that award and deduct points for a team, with a Google Sheet as the
single source of truth. Every operation is confirmed, logged and duplicate-proof.

| Bot | Can do |
|---|---|
| **Bot #1 — Deduct bot** | deduct points only |
| **Bot #2 — Points bot** | award **and** deduct points |

**Features**

- **Access control:** only people listed in the `Users` sheet can use the bots. Their role (`admin`, `operator`, `member`) decides what they may do. Everyone else gets a polite refusal that includes their Telegram ID, so they can send it to the administrator.
- **Append-only ledger:** every operation becomes one row in `Transactions` (ID, timestamp, bot, operator, target, type, amount, note). The bots never edit or delete rows.
- **Balances that cannot drift:** the `Balances` tab contains only formulas computed from `Transactions`. The bots never write to it.
- **No duplicates:** each transaction ID is derived from the Telegram message that started it. Combined with a Yes/No confirmation step, this means retries, double taps and re-delivered updates never create a second row.
- **No accidental negative balances:** a deduction that would go below zero is refused. An admin can explicitly allow it, either per person (`allow_negative`) or per operation with a "Yes, allow negative" button.
- **MOCK mode:** an in-memory sheet plus a terminal chat simulator, so everything can be tried without Google or Telegram.
- **Tests:** 88 pytest tests covering permissions, duplicates, balances, the Sheets mapping and the Telegram handlers.

---

## Contents

1. [Try it in 1 minute (MOCK mode)](#try-it-in-1-minute-mock-mode)
2. [Full setup](#full-setup)
3. [Commands](#commands)
4. [Administrator guide](docs/ADMIN_GUIDE.md)
5. [How it works](#how-it-works)
6. [Running in production](#running-in-production)
7. [Development](#development)

---

## Try it in 1 minute (MOCK mode)

Requires Python 3.10+.

```bash
git clone https://github.com/Paul0256/telegram-points-bot.git
cd telegram-points-bot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

python -m pointsbot demo           # scripted walkthrough of every feature
python -m pointsbot console        # chat with both bots yourself
```

In the console, type bot commands exactly as you would in Telegram (`/deduct Mia 5 coffee`).
Lines starting with `:` control the simulator:

| Console command | Effect |
|---|---|
| `:bot deduct` / `:bot points` | switch between bot #1 and bot #2 |
| `:as 1002` | act as another Telegram user (`:users` lists them) |
| `:yes` `:no` `:override` | press a confirmation button |
| `:tap` | press the same button again (double tap) |
| `:retry` | re-deliver the last command, as Telegram does after a network error |
| `:sheet` | show the Transactions and Balances tables |

Demo people: Anna (admin, `1001`), Oscar (operator, `1002`), Mia (member, `1003`),
Leo (member without Telegram), Nora (member, `1004`, allowed to go negative).
Any other ID, e.g. `9999`, is a stranger.

---

## Full setup

You need: two Telegram bots, a Google Cloud service account, and a Google Sheet.
Allow about 20 minutes.

### 1. Create the two bots with @BotFather

1. In Telegram, open **@BotFather** and send `/newbot`.
2. Name: `Team Deduct Bot`. Username: something unique ending in `bot`, e.g. `acme_deduct_bot`.
3. BotFather replies with a **token** like `7012345678:AAH...`. This is bot #1's token.
4. Repeat for bot #2: `/newbot`, name `Team Points Bot`, username e.g. `acme_points_bot`. Keep this second token.
5. Optional: set a description and profile photo with `/setdescription` and `/setuserpic`.

> Treat tokens like passwords. If one leaks, use `/revoke` in BotFather and update `.env`.

### 2. Find your Telegram user ID

Either:
- message **@userinfobot** and it replies with your numeric ID, or
- finish the setup first, then send `/myid` to either bot. It answers everyone, including people who don't have access yet.

### 3. Google Cloud: project, Sheets API, service account

1. Open <https://console.cloud.google.com/> and sign in with a Google account.
2. Create a project: click the project picker at the top, then **New project**, name it `points-bot`, then **Create**. Make sure the new project is selected.
3. Enable the API: go to **APIs & Services → Library**, search for **Google Sheets API**, then click **Enable**. The Drive API is *not* needed.
4. Create the service account: go to **IAM & Admin → Service Accounts → Create service account**.
   - Name: `points-bot`, then **Create and continue**.
   - Roles: leave empty (access is granted by sharing the sheet), then **Continue → Done**.
5. Create the key: click the new service account, open the **Keys** tab, then **Add key → Create new key → JSON → Create**.
   A `.json` file downloads. Save it as `service_account.json` in the project folder. It is git-ignored, so never commit it.
6. Copy the service account's **email**. It looks like `points-bot@points-bot-123456.iam.gserviceaccount.com` and is also inside the JSON as `client_email`.

> If key creation is blocked ("Service account key creation is disabled"), your Google
> Workspace organisation enforces `iam.disableServiceAccountKeyCreation`. Ask the workspace admin to allow it for this
> project, or use a personal Google account.

### 4. Create and share the Google Sheet

1. Create an empty sheet at <https://sheets.new> and name it, for example, `Team Points`.
2. Click **Share**, paste the service account email, choose **Editor**, untick "Notify people", and click **Share**.
3. Copy the sheet ID from the URL: `https://docs.google.com/spreadsheets/d/`**`1AbC...xyz`**`/edit`.

### 5. Configure `.env`

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

Fill in `DEDUCT_BOT_TOKEN`, `POINTS_BOT_TOKEN`, `SPREADSHEET_ID` and, if you saved the key elsewhere,
`GOOGLE_SERVICE_ACCOUNT_FILE`. Keep `MOCK=false`.

### 6. Create the tabs and make yourself admin

```bash
python -m pointsbot setup-sheet --admin-id 123456789 --admin-name "Your Name"
```

This creates `Users`, `Transactions` and `Balances` with headers, formulas, a role drop-down,
checkboxes and an "are you sure?" warning on the bot-managed tabs. It is safe to run again and never deletes data.

### 7. Check and run

```bash
python -m pointsbot check          # validates tokens, sheet access and the Users tab
python -m pointsbot run            # starts both bots; stop with Ctrl+C
```

Open each bot in Telegram and send `/start`. Then add your team to the `Users` tab, as described in the
[administrator guide](docs/ADMIN_GUIDE.md).

---

## Commands

| Command | Who | What it does |
|---|---|---|
| `/award <name> <amount> [note]` | operator, admin, **bot #2 only** | Award points (asks for confirmation) |
| `/deduct <name> <amount> [note]` | operator, admin | Deduct points (asks for confirmation) |
| `/balance` | everyone | Own balance; admins see **everyone** |
| `/balance <name>` | admin (or yourself) | One person's balance |
| `/history` | everyone | Last 10 transactions: own for members and operators, all for admins |
| `/history <name>` | admin (or yourself) | Last 10 transactions of one person |
| `/myid` | anyone | Shows your Telegram ID |
| `/help`, `/start` | authorized users | Role-aware help |

Names can contain spaces and are case-insensitive. A unique prefix is enough: `/deduct ann 5` works for "Anna".

```
/deduct Mia 10 late for the meeting
→ Deduct 10 points from Mia?
  Balance: 50 → 40
  Note: late for the meeting
  [✅ Yes] [❌ No]
→ ✅ Done. Deducted 10 points from Mia. New balance: 40
```

---

## How it works

```
 Telegram ──► Bot #1 (deduct) ─┐
                               ├─► CommandHandler ─► PointsService ─► Storage ─► Google Sheet
 Telegram ──► Bot #2 (points) ─┘    (parsing,         (rules, lock,     (gspread    Users
                                     replies)          confirmation)     or memory)  Transactions
                                                                                     Balances (formulas)
```

- **One process, two bots.** Both bots are polled by one aiogram dispatcher and share one `PointsService`. A single lock serializes writes, so the two bots never race on the same balance.
- **Transaction ID** = `<bot>-<chat>-<message id>`, e.g. `deduct-u123456789-42`. The same Telegram message always yields the same ID. Before writing, the service checks that the ID is not in `Transactions` yet.
- **Confirmation flow:** the command is validated (permissions, person, amount, balance) and parked for 5 minutes. On **Yes**, everything is re-validated against fresh sheet data and one row is appended. A second tap finds the row and answers "already recorded"; **No** discards the request.
- **Resilience:** transient Google errors (rate limits, 5xx, network) are retried with backoff. Before each retried append, the code checks whether the previous attempt actually landed, so a lost response cannot create a duplicate row. If Google stays down, the operator gets a pop-up and can press Yes again later.
- **Safety:** values are written with `valueInputOption=RAW`, so a note such as `=IMPORTXML(...)` is stored as text, never as a formula. All user text is HTML-escaped in replies.
- **Balances** are computed in Python from `Transactions` for the below-zero check, and by these formulas for humans:
  `=BYROW(A2:A, LAMBDA(n, IF(n="", "", SUMIFS(Transactions!H2:H, Transactions!F2:F, n, Transactions!G2:G, "award") - SUMIFS(…, "deduct"))))`

### Project structure

```
telegram-points-bot/
├── pointsbot/
│   ├── __main__.py          # CLI: run | console | demo | setup-sheet | check
│   ├── config.py            # .env loading and validation
│   ├── models.py            # User, Role, Transaction, bot profiles
│   ├── permissions.py       # role / bot permission matrix
│   ├── service.py           # business rules, confirmation, duplicate protection
│   ├── commands.py          # command parsing and reply texts (transport-agnostic)
│   ├── mock_console.py      # MOCK mode simulator and scripted demo
│   ├── storage/
│   │   ├── base.py          # Storage protocol
│   │   ├── memory.py        # in-memory sheet + demo data
│   │   └── gsheets.py       # Google Sheets backend and sheet setup
│   └── telegram/
│       ├── app.py           # starts both bots in one dispatcher
│       └── handlers.py      # aiogram handlers and inline buttons
├── tests/                   # pytest suite
├── docs/ADMIN_GUIDE.md
├── .env.example
└── requirements.txt
```

---

## Running in production

- **Run exactly one instance.** Telegram allows only one poller per bot token, and the duplicate lock lives in that process.
- **Keep it running.** Example systemd unit on a Linux VPS (`/etc/systemd/system/points-bot.service`):

  ```ini
  [Unit]
  Description=Telegram points bots
  After=network-online.target

  [Service]
  WorkingDirectory=/opt/points-bot
  ExecStart=/opt/points-bot/.venv/bin/python -m pointsbot run
  Restart=always
  RestartSec=5
  User=pointsbot

  [Install]
  WantedBy=multi-user.target
  ```

  Then run `sudo systemctl enable --now points-bot` and view logs with `journalctl -u points-bot -f`.
- **Restarts are safe.** Unconfirmed operations expire on restart (the user just repeats the command). Messages sent while the bot was offline are processed when it comes back, with duplicates filtered.
- **Scale:** every operation reads the `Transactions` tab, which is comfortable up to tens of thousands of rows. For much larger ledgers, archive old years to another tab and carry the balance forward with one transaction per person.

## Development

```bash
pip install -r requirements-dev.txt
pytest                              # 88 tests, no network needed
```

Tests use the in-memory storage, a fake of the gspread API, and a fake Telegram Bot API session
fed through aiogram's real dispatcher. No tokens or Google credentials are needed.

## License

MIT
