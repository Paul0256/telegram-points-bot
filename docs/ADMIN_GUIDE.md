# Administrator guide

This guide is for the person who manages the points system day to day. No programming needed:
everything is done in the Google Sheet and in Telegram.

## The three tabs

| Tab | Who edits it | Purpose |
|---|---|---|
| **Users** | **you (admin)** | Who exists, who may use the bots, and with what role |
| **Transactions** | the bots only | Append-only ledger of every award and deduction |
| **Balances** | nobody (formulas) | Current balance per person, recalculated automatically |

`Transactions` and `Balances` show a warning if someone tries to edit them by hand. Please respect it.

## Managing people (the `Users` tab)

| Column | Meaning |
|---|---|
| `telegram_id` | Numeric Telegram ID. Leave **empty** for people who only receive points and don't use the bots. |
| `name` | Name used in commands and in the ledger. **Must be unique.** |
| `role` | `admin`, `operator` or `member` (drop-down) |
| `allow_negative` | ☑ = this person's balance may go below zero |
| `active` | ☐ = the person can no longer use the bots or receive points; their history stays |
| `comment` | Free text for yourself; the bots ignore it |

### Roles

| | member | operator | admin |
|---|:-:|:-:|:-:|
| See own balance and history | ✅ | ✅ | ✅ |
| Deduct points (bot #1 or #2) | – | ✅ | ✅ |
| Award points (bot #2 only) | – | ✅ | ✅ |
| See everyone's balances and history | – | – | ✅ |
| Push a balance below zero (with explicit confirmation) | – | – | ✅ |

Bot #1 can **never** award, whatever the role.

### Add a person

1. Ask them to send `/myid` to either bot. The bot answers even if they have no access yet.
2. Add a row in `Users`: their ID, a unique name, a role, and tick `active`.
3. They can use the bots within about 30 seconds (the bot caches the Users tab briefly).

### Remove a person

Untick `active` and **do not delete the row**. Their past transactions keep pointing to their name, and
`Balances` keeps showing their final balance.

### Rename a person

Names are the link between `Users` and `Transactions`. If you rename someone in `Users`, their old
transactions no longer count towards the new name. Prefer keeping the original name. If you must rename:

1. Note their balance, e.g. 40.
2. Change the name.
3. Award 40 points to the new name with the note `carried over from <old name>`.

Also avoid the characters `*`, `?` and `~` in names, because Google Sheets treats them as wildcards in formulas.

## Fixing mistakes

**Never edit or delete rows in `Transactions`.** That would break the audit trail.
To correct a wrong operation, record the opposite one with a clear note instead:

- 10 points were deducted by mistake → `/award Mia 10 correction of deduct-u123-45`
- Points were awarded to the wrong person → deduct from the wrong one, award to the right one, and mention the original ID in both notes.

The transaction ID is shown in the bot's "Done" message and in column A of `Transactions`.

## Negative balances

By default, a deduction that would take a balance below zero is refused. Two ways to allow it:

- **Per person:** tick `allow_negative` in `Users`. Operators can then deduct below zero for that person.
- **Per operation (admins only):** when an admin's deduction would go below zero, the bot shows
  **"⚠️ Yes, allow negative"** instead of the normal Yes. Pressing it records the operation.

## Everyday checks

- `/balance` (as admin): everyone's balance in one message.
- `/history` or `/history Mia`: the last 10 transactions.
- The `Balances` tab: the same numbers, plus totals awarded and deducted, and the number of transactions.
- **File → Version history** in Google Sheets: a full backup history. You can also make a copy of the sheet monthly.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Sorry, you don't have access" for a team member | Check their `telegram_id` (no spaces, correct number) and that `active` is ticked. Wait 30 seconds. |
| "No active person called …" | Check the spelling, or whether the person is inactive. |
| "matches several people" | Type more of the name. |
| Bot doesn't answer at all | The bot program is not running. Ask your technical contact to check `python -m pointsbot check` and the logs. |
| "Could not reach the sheet" pop-up | Google was briefly unavailable. Press Yes again; duplicates are impossible. |
| A row in Users seems ignored | Run `python -m pointsbot check`. It lists rows with invalid roles or IDs. |
