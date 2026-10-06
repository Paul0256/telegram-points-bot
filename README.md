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
- **No duplicates:** each transaction ID is derived from the message that started it. Combined with a Yes/No confirmation step, this means retries, double taps and re-delivered updates never create a second row.
- **No accidental negative balances:** a deduction that would go below zero is refused. An admin can explicitly allow it, either per person (`allow_negative`) or per operation with a "Yes, allow negative" button.
- **MOCK mode:** an in-memory sheet plus a terminal chat simulator, so everything can be tried without Google or Telegram.
- **Platform-independent core:** permissions, points logic, duplicate protection and Google Sheets access live in a core package that never imports Telegram code. Telegram is just one thin adapter, so Discord, Slack or WhatsApp adapters can be added without touching the core (see [How to add another platform](#how-to-add-another-platform)).
- **Tests:** 100 pytest tests covering permissions, duplicates, balances, the Sheets mapping, the Telegram adapter and the core/adapter boundary.

---

## Contents

1. [Try it in 1 minute (MOCK mode)](#try-it-in-1-minute-mock-mode)
2. [Full setup](#full-setup)
3. [Commands](#commands)
4. [Administrator guide](docs/ADMIN_GUIDE.md)
5. [How it works](#how-it-works)
6. [How to add another platform](#how-to-add-another-platform)
7. [Running in production](#running-in-production)
8. [Development](#development)

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
python -m pointsbot console        # chat with both bots yourself (console adapter)
```

Later, with `.env` filled in, `python -m pointsbot console --sheets` runs the same console against the real Google Sheet.

In the console, type bot commands exactly as you would in Telegram (`/deduct Mia 5 coffee`).
Lines starting with `:` control the simulator:

| Console command | Effect |
|---|---|
| `:bot deduct` / `:bot points` | switch between bot #1 and bot #2 |
| `:as 1002` / `:as discord:42` | act as another user: a Telegram ID, or `<platform>:<id>` (`:users` lists them) |
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
        ADAPTERS (thin, per platform)                    CORE (platform-independent)
 ┌──────────────────────────────────────┐      ┌────────────────────────────────────────────────┐
 │ Telegram: Bot #1 (deduct), Bot #2    │      │ CommandProcessor  parse args, build replies    │
 │ Console:  terminal REPL / demo       │─────►│ PointsService     rules, lock, confirmation    │──► Google Sheet
 │ (Discord, Slack, WhatsApp: later)    │◄─────│ Storage           Google Sheets or in-memory   │    Users
 └──────────────────────────────────────┘      └────────────────────────────────────────────────┘    Transactions
     IncomingCommand / IncomingAction  ──►  Response (rich text + buttons)                           Balances
```

- **Core / adapter contract.** An adapter turns a platform event into an `IncomingCommand` (sender identity, stable event ID, command, arguments) or an `IncomingAction` (button press). The core returns a `Response`: rich text spans (bold, code, ...) plus buttons. The core never produces HTML or Markdown. Each adapter renders the spans for its platform and escapes user text, so the core never deals with platform formatting. `tests/test_architecture.py` fails if anything in `pointsbot/core` imports a messaging library or an adapter.
- **Identities.** A sender is `Identity(platform, user_id)`, e.g. `telegram:123456789`. The `Users` sheet has one `<platform>_id` column per platform, so one person can use several platforms and still has a single balance.
- **One process, two bots.** Both bots are polled by one aiogram dispatcher and share one `PointsService`. A single lock serializes writes, so the two bots never race on the same balance.
- **Transaction ID** = `<bot>-<adapter event ID>`, e.g. `deduct-tg-u123456789-42` for Telegram message 42 in chat 123456789. The same message always yields the same ID. Before writing, the service checks that the ID is not in `Transactions` yet.
- **Confirmation flow:** the command is validated (permissions, person, amount, balance) and parked for 5 minutes. On **Yes**, everything is re-validated against fresh sheet data and one row is appended. A second tap finds the row and answers "already recorded"; **No** discards the request.
- **Resilience:** transient Google errors (rate limits, 5xx, network) are retried with backoff. Before each retried append, the code checks whether the previous attempt actually landed, so a lost response cannot create a duplicate row. If Google stays down, the operator gets a pop-up and can press Yes again later.
- **Safety:** values are written with `valueInputOption=RAW`, so a note such as `=IMPORTXML(...)` is stored as text, never as a formula. Adapters escape all user text when rendering replies (HTML for Telegram).
- **Balances** are computed in Python from `Transactions` for the below-zero check, and by these formulas for humans:
  `=BYROW(A2:A, LAMBDA(n, IF(n="", "", SUMIFS(Transactions!H2:H, Transactions!F2:F, n, Transactions!G2:G, "award") - SUMIFS(…, "deduct"))))`

### Project structure

```
telegram-points-bot/
├── pointsbot/
│   ├── __main__.py              # CLI: run | console | demo | setup-sheet | check
│   ├── config.py                # .env loading and validation
│   ├── core/                    # platform-independent, no messaging imports
│   │   ├── messages.py          # adapter contract: IncomingCommand/Action, Response, rich text
│   │   ├── commands.py          # CommandProcessor: argument parsing and reply texts
│   │   ├── service.py           # business rules, confirmation, duplicate protection
│   │   ├── permissions.py       # role / bot permission matrix
│   │   ├── models.py            # Identity, User, Role, Transaction, bot profiles
│   │   └── storage/
│   │       ├── base.py          # Storage protocol
│   │       ├── memory.py        # in-memory sheet + demo data (MOCK mode)
│   │       └── gsheets.py       # Google Sheets backend and sheet setup
│   └── adapters/                # thin translators, one per platform
│       ├── console.py           # terminal adapter + scripted demo
│       └── telegram/
│           ├── app.py           # starts both bots in one aiogram dispatcher
│           ├── handlers.py      # Telegram update -> core call -> reply / buttons
│           └── render.py        # rich text -> Telegram HTML
├── tests/                   # pytest suite
├── docs/ADMIN_GUIDE.md
├── .env.example
└── requirements.txt
```

---

## How to add another platform

The core doesn't change. Adding, for example, Discord takes four steps:

1. **Identities:** add a `discord_id` column to the `Users` tab. Any column named `<platform>_id` is picked up automatically, and a person with both a Telegram and a Discord ID has one balance.
2. **Adapter:** create `pointsbot/adapters/discord/` and translate events to the core contract:

   ```python
   from pointsbot.core import CommandProcessor, Identity, IncomingAction, IncomingCommand, POINTS_BOT

   processor = CommandProcessor(service, POINTS_BOT)   # one processor per bot profile

   async def on_command(interaction):                  # discord.py slash command (sketch)
       cmd = IncomingCommand(
           sender=Identity("discord", str(interaction.user.id)),
           event_id=f"dc-{interaction.id}",            # stable on retries, unique on the platform
           command=interaction.command.name,           # "award", "deduct", "balance", ...
           args=interaction.namespace.text or "",
       )
       response = await asyncio.to_thread(processor.handle_command, cmd)   # core is blocking
       await interaction.response.send_message(to_markdown(response.text), view=to_buttons(response.buttons))

   async def on_button(interaction, action, token):    # a button created from response.buttons
       response = await asyncio.to_thread(
           processor.handle_action, IncomingAction(Identity("discord", str(interaction.user.id)), action, token))
       ...  # replace the confirmation message, or show a pop-up if response.alert_only
   ```
3. **Rendering:** write `to_markdown(rich_text)` like `adapters/telegram/render.py`: map `Style.BOLD`, `CODE`, `PRE`, ... to the platform's markup and **escape every span**. Map each `Button` to a platform button that carries its `action` and `token` (under 48 characters).
4. **Wiring and tests:** start the adapter from `__main__.py`, in the same process as the others, so the duplicate lock is shared. Add tests modelled on `tests/test_telegram.py`. `tests/test_architecture.py` will fail if a platform import leaks into the core.

Rules for a correct `event_id`: it must stay the same when the platform re-delivers the same event, and it must be unique within the platform. Prefix it with a short platform tag (`tg-`, `dc-`, `sl-`, `wa-`). Platforms without buttons (e.g. plain SMS) can map "reply YES" to `IncomingAction(sender, "yes", token)`.

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
pytest                              # 100 tests, no network needed
```

Tests use the in-memory storage, a fake of the gspread API, and a fake Telegram Bot API session
fed through aiogram's real dispatcher. No tokens or Google credentials are needed. An architecture test
guarantees that `pointsbot/core` never imports platform code.

## License

MIT
