"""Google Sheets storage (gspread + service account).

Sheet layout (created by `python -m pointsbot setup-sheet`):

    Users         telegram_id | name | role | allow_negative | active | comment [| discord_id | slack_id ...]
    Transactions  tx_id | timestamp_utc | bot | operator_id | operator_name | target | type | amount | note
    Balances      name | balance | awarded | deducted | transactions   (formulas only)

The bot only ever *appends* to "Transactions" and never writes "Balances".
Values are written with valueInputOption=RAW, so a note like "=HYPERLINK(...)"
is stored as plain text and can never become a formula.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import gspread
from gspread.exceptions import APIError, WorksheetNotFound
from gspread.utils import InsertDataOption, ValueInputOption, ValueRenderOption
from requests.exceptions import RequestException

from ..models import Role, Transaction, TxType, User

log = logging.getLogger(__name__)

USERS_SHEET = "Users"
TRANSACTIONS_SHEET = "Transactions"
BALANCES_SHEET = "Balances"

USERS_HEADER = ["telegram_id", "name", "role", "allow_negative", "active", "comment"]
# Any Users column called "<platform>_id" holds that platform's user IDs, so a
# new messaging platform needs a new column, not a code change in the core.
ID_COLUMN_SUFFIX = "_id"
TRANSACTIONS_HEADER = [
    "tx_id",
    "timestamp_utc",
    "bot",
    "operator_id",
    "operator_name",
    "target",
    "type",
    "amount",
    "note",
]
BALANCES_HEADER = ["name", "balance", "awarded", "deducted", "transactions"]

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # no Drive access needed
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_TRUE = {"true", "yes", "y", "1", "x", "✓", "taip", "да"}
_FALSE = {"false", "no", "n", "0", "", "ne", "нет"}

T = TypeVar("T")


class SheetFormatError(ValueError):
    """A row in the sheet cannot be understood."""


# --------------------------------------------------------------------------- row mapping


def parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or str(value).strip() == "":
        return default
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise SheetFormatError(f"expected TRUE/FALSE, got {value!r}")


def parse_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise SheetFormatError(f"{field}: expected a whole number, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value).strip().replace(" ", "")
    if text.lstrip("-").isdigit():
        return int(text)
    raise SheetFormatError(f"{field}: expected a whole number, got {value!r}")


def _pad(row: Sequence[Any], length: int) -> list[Any]:
    return list(row) + [""] * (length - len(row))


def parse_platform_id(value: Any) -> str:
    """Sheet cell -> platform user ID string ("" if empty). 123456789.0 -> "123456789"."""
    if isinstance(value, bool):
        raise SheetFormatError(f"expected a user ID, got {value!r}")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value).strip()


def parse_user_row(row: Sequence[Any], header: Sequence[str] = USERS_HEADER) -> User | None:
    """Parse one Users row by column *name*. None for empty rows; SheetFormatError for broken ones."""
    cells = dict(zip(header, _pad(row, len(header)), strict=False))
    name = cells.get("name")
    name = "" if name is None else str(name)
    if not name.strip():
        return None
    try:
        parsed_role = Role.parse(cells.get("role", ""))
    except ValueError as exc:
        raise SheetFormatError(str(exc)) from None
    ids = {}
    for column, value in cells.items():
        if column.endswith(ID_COLUMN_SUFFIX) and column != ID_COLUMN_SUFFIX:
            user_id = parse_platform_id(value)
            if user_id:
                ids[column[: -len(ID_COLUMN_SUFFIX)]] = user_id
    return User(
        name=name,
        role=parsed_role,
        ids=ids,
        allow_negative=parse_bool(cells.get("allow_negative"), default=False),
        active=parse_bool(cells.get("active"), default=True),
    )


def normalize_header(row: Sequence[Any]) -> list[str]:
    return [str(cell).strip().lower() for cell in row]


def transaction_to_row(tx: Transaction) -> list[Any]:
    return [
        tx.tx_id,
        tx.timestamp.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT),
        tx.bot,
        tx.operator_id,
        tx.operator_name,
        tx.target,
        tx.type.value,
        tx.amount,
        tx.note,
    ]


def parse_timestamp(value: Any) -> datetime:
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, TIMESTAMP_FORMAT)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise SheetFormatError(f"timestamp_utc: cannot read {value!r}") from None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def parse_transaction_row(row: Sequence[Any]) -> Transaction | None:
    tx_id, ts, bot, operator_id, operator_name, target, tx_type, amount, note = _pad(row, len(TRANSACTIONS_HEADER))[
        : len(TRANSACTIONS_HEADER)
    ]
    if str(tx_id).strip() == "":
        return None
    try:
        parsed_type = TxType(str(tx_type).strip().lower())
    except ValueError:
        raise SheetFormatError(f"type: expected award or deduct, got {tx_type!r}") from None
    parsed_amount = parse_int(amount, "amount")
    if parsed_amount <= 0:
        raise SheetFormatError(f"amount must be positive, got {amount!r}")
    return Transaction(
        tx_id=str(tx_id).strip(),
        timestamp=parse_timestamp(ts),
        bot=str(bot),
        operator_id=parse_platform_id(operator_id),
        operator_name=str(operator_name),
        target=str(target),
        type=parsed_type,
        amount=parsed_amount,
        note=str(note),
    )


# --------------------------------------------------------------------------- retries


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, APIError):
        return getattr(exc, "code", None) in RETRYABLE_STATUS
    return isinstance(exc, RequestException)


def with_retries(
    fn: Callable[[], T], *, attempts: int = 4, base_delay: float = 1.0, sleep: Callable[[float], None] = time.sleep
) -> T:
    """Retry transient Google API failures (rate limits, 5xx, network) with backoff."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if not _is_retryable(exc) or attempt == attempts - 1:
                raise
            delay = base_delay * 2**attempt
            log.warning("Google Sheets call failed (%s), retrying in %.0fs", exc, delay)
            sleep(delay)
    raise AssertionError("unreachable")


# --------------------------------------------------------------------------- storage


class GoogleSheetsStorage:
    def __init__(
        self,
        spreadsheet: gspread.Spreadsheet,
        *,
        users_cache_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.spreadsheet = spreadsheet
        self.users_ws = spreadsheet.worksheet(USERS_SHEET)
        self.transactions_ws = spreadsheet.worksheet(TRANSACTIONS_SHEET)
        self._users_cache_seconds = users_cache_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._users: list[User] | None = None
        self._users_loaded_at = 0.0
        self._lock = threading.Lock()
        self.warnings: list[str] = []  # problems found in the sheet, for `check`

    @classmethod
    def connect(cls, service_account_file: Path | str, spreadsheet_id: str, **kwargs: Any) -> GoogleSheetsStorage:
        return cls(open_spreadsheet(service_account_file, spreadsheet_id), **kwargs)

    def _retry(self, fn: Callable[[], T]) -> T:
        return with_retries(fn, sleep=self._sleep)

    # The "Users" sheet changes rarely and is read on every message, so it is
    # cached briefly. Transactions are always read fresh (balances must be exact).
    def list_users(self) -> list[User]:
        with self._lock:
            fresh = self._users is not None and self._monotonic() - self._users_loaded_at < self._users_cache_seconds
            if not fresh:
                rows = self._retry(
                    lambda: self.users_ws.get_all_values(value_render_option=ValueRenderOption.unformatted)
                )
                header = normalize_header(rows[0]) if rows else []
                missing = [c for c in ("name", "role") if c not in header]
                if missing:
                    raise SheetFormatError(f"{USERS_SHEET} tab is missing the column(s): {', '.join(missing)}")
                self._users = self._parse_rows(rows, lambda row: parse_user_row(row, header), USERS_SHEET)
                self._users_loaded_at = self._monotonic()
            return list(self._users)

    def invalidate_users_cache(self) -> None:
        with self._lock:
            self._users = None

    def list_transactions(self) -> list[Transaction]:
        rows = self._retry(
            lambda: self.transactions_ws.get_all_values(value_render_option=ValueRenderOption.unformatted)
        )
        return self._parse_rows(rows, parse_transaction_row, TRANSACTIONS_SHEET)

    def transaction_exists(self, tx_id: str) -> bool:
        ids = self._retry(lambda: self.transactions_ws.col_values(1))
        return tx_id in ids[1:]

    def append_transaction(self, tx: Transaction) -> None:
        """Append one row; a retry first checks whether the previous attempt landed."""
        row = transaction_to_row(tx)
        attempts = 4
        for attempt in range(attempts):
            try:
                if attempt > 0 and self.transaction_exists(tx.tx_id):
                    log.info("transaction %s was written by a previous attempt", tx.tx_id)
                    return
                self.transactions_ws.append_row(
                    row,
                    value_input_option=ValueInputOption.raw,
                    insert_data_option=InsertDataOption.insert_rows,
                    table_range="A1",
                )
                return
            except Exception as exc:
                if not _is_retryable(exc) or attempt == attempts - 1:
                    raise
                delay = 2**attempt
                log.warning("append of %s failed (%s), retrying in %ss", tx.tx_id, exc, delay)
                self._sleep(delay)

    def _parse_rows(self, rows: list[list[Any]], parse: Callable[[Sequence[Any]], Any], sheet: str) -> list[Any]:
        result, warnings = [], []
        for number, row in enumerate(rows[1:], start=2):  # row 1 is the header
            try:
                item = parse(row)
            except SheetFormatError as exc:
                warnings.append(f"{sheet} row {number}: {exc} — row ignored")
                continue
            if item is not None:
                result.append(item)
        for warning in warnings:
            log.warning(warning)
        self.warnings = [w for w in self.warnings if not w.startswith(sheet + " ")] + warnings
        return result


def open_spreadsheet(service_account_file: Path | str, spreadsheet_id: str, timeout: float = 30) -> gspread.Spreadsheet:
    client = gspread.service_account(filename=str(service_account_file), scopes=SCOPES)
    client.set_timeout(timeout)
    return client.open_by_key(spreadsheet_id)


# --------------------------------------------------------------------------- setup

# Formulas use English function names and commas, which the Sheets API accepts
# regardless of the spreadsheet's locale.
_SUM = 'SUMIFS(Transactions!H2:H, Transactions!F2:F, n, Transactions!G2:G, "{type}")'
BALANCES_FORMULAS = [
    '=IFERROR(SORT(UNIQUE(FILTER(Users!B2:B, Users!B2:B<>""))), "")',
    f'=BYROW(A2:A, LAMBDA(n, IF(n="", "", {_SUM.format(type="award")} - {_SUM.format(type="deduct")})))',
    f'=BYROW(A2:A, LAMBDA(n, IF(n="", "", {_SUM.format(type="award")})))',
    f'=BYROW(A2:A, LAMBDA(n, IF(n="", "", {_SUM.format(type="deduct")})))',
    '=BYROW(A2:A, LAMBDA(n, IF(n="", "", COUNTIF(Transactions!F2:F, n))))',
]
PROTECTION_NOTE = "Managed by the points bots. Do not edit by hand."
DEFAULT_SHEET_NAMES = {"sheet1", "lapas1", "лист1"}
OUR_TABS = {USERS_SHEET, TRANSACTIONS_SHEET, BALANCES_SHEET}


class SetupError(RuntimeError):
    pass


def setup_spreadsheet(spreadsheet: gspread.Spreadsheet, admin: tuple[int, str] | None = None) -> list[str]:
    """Create/verify the three tabs. Safe to run repeatedly; never deletes data."""
    done: list[str] = []
    existing = {ws.title: ws for ws in spreadsheet.worksheets()}
    # A brand-new spreadsheet has one empty tab whose name depends on the UI
    # language ("Sheet1", "Lapas1", ...); reuse it instead of leaving it behind.
    only_tab = next(iter(existing.values())) if len(existing) == 1 else None

    def ensure(title: str, header: list[str], cols: int) -> gspread.Worksheet:
        ws = existing.get(title)
        if ws is None:
            spare = [
                w
                for t, w in existing.items()
                if t not in OUR_TABS and (t.lower() in DEFAULT_SHEET_NAMES or w is only_tab) and not w.get_all_values()
            ]
            if spare:
                ws = spare[0]
                del existing[ws.title]
                ws.update_title(title)
                done.append(f"Renamed the empty default tab to {title}")
            else:
                ws = spreadsheet.add_worksheet(title, rows=1000, cols=cols)
                done.append(f"Created tab {title}")
            existing[title] = ws
        current = [str(v) for v in ws.row_values(1)]
        if not any(current):
            ws.update([header], "A1", value_input_option=ValueInputOption.raw)
            done.append(f"Wrote headers in {title}")
        elif current[: len(header)] != header:
            raise SetupError(
                f'Tab "{title}" already has different headers: {current}. Expected: {header}. '
                "Rename that tab or fix the headers, then run setup again."
            )
        ws.freeze(rows=1)
        ws.format("1:1", {"textFormat": {"bold": True}})
        return ws

    users = ensure(USERS_SHEET, USERS_HEADER, len(USERS_HEADER))
    transactions = ensure(TRANSACTIONS_SHEET, TRANSACTIONS_HEADER, len(TRANSACTIONS_HEADER))
    balances = ensure(BALANCES_SHEET, BALANCES_HEADER, len(BALANCES_HEADER))

    balances.update([BALANCES_FORMULAS], "A2", value_input_option=ValueInputOption.user_entered)
    done.append("Wrote Balances formulas")

    spreadsheet.batch_update(
        {"requests": _validation_requests(users.id) + _protection_requests(spreadsheet, [transactions.id, balances.id])}
    )
    done.append("Added role drop-down, checkboxes and edit warnings")

    if admin is not None:
        done.append(_add_first_admin(users, admin))
    return done


def _add_first_admin(users: gspread.Worksheet, admin: tuple[int, str]) -> str:
    """Write the admin into the first row without a name.

    Checkbox columns make Google fill empty rows with FALSE, so "is the tab
    empty?" must look at the name column, and append_row would land after
    row 1000. Writing to an explicit row avoids both.
    """
    telegram_id, name = admin
    rows = users.get_all_values()
    named = [r for r in rows[1:] if len(r) > 1 and str(r[1]).strip()]
    if any(str(r[0]).strip() == str(telegram_id) for r in named):
        return f"Admin {telegram_id} is already in Users"
    if named:
        return "Users already has people; add yourself as admin by hand if needed"
    users.update(
        [[telegram_id, name, "admin", False, True, "created by setup"]], "A2", value_input_option=ValueInputOption.raw
    )
    return f"Added first admin {name} ({telegram_id})"


def _validation_requests(users_sheet_id: int) -> list[dict[str, Any]]:
    def rng(col_start: int, col_end: int) -> dict[str, int]:
        return {
            "sheetId": users_sheet_id,
            "startRowIndex": 1,
            "endRowIndex": 1000,
            "startColumnIndex": col_start,
            "endColumnIndex": col_end,
        }

    roles = [{"userEnteredValue": r.value} for r in Role]
    return [
        {
            "setDataValidation": {
                "range": rng(2, 3),
                "rule": {"condition": {"type": "ONE_OF_LIST", "values": roles}, "strict": True, "showCustomUi": True},
            }
        },
        {"setDataValidation": {"range": rng(3, 5), "rule": {"condition": {"type": "BOOLEAN"}}}},
    ]


def _protection_requests(spreadsheet: gspread.Spreadsheet, sheet_ids: list[int]) -> list[dict[str, Any]]:
    requests = []
    for sheet_id in sheet_ids:
        already = any(p.get("description") == PROTECTION_NOTE for p in spreadsheet.list_protected_ranges(sheet_id))
        if not already:
            requests.append(
                {
                    "addProtectedRange": {
                        "protectedRange": {
                            "range": {"sheetId": sheet_id},
                            "description": PROTECTION_NOTE,
                            "warningOnly": True,
                        }
                    }
                }
            )
    return requests


def check_spreadsheet(spreadsheet: gspread.Spreadsheet) -> None:
    """Raise SetupError if a required tab is missing."""
    for title in (USERS_SHEET, TRANSACTIONS_SHEET, BALANCES_SHEET):
        try:
            spreadsheet.worksheet(title)
        except WorksheetNotFound:
            raise SetupError(f'Tab "{title}" is missing. Run: python -m pointsbot setup-sheet') from None
