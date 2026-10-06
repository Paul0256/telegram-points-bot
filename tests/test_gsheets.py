"""Google Sheets storage tested against an in-process fake of the gspread API."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import requests
from gspread.exceptions import APIError, WorksheetNotFound

from pointsbot.core.models import DEDUCT_BOT, Identity, Role, Transaction, TxType
from pointsbot.core.service import CommitStatus, PointsService
from pointsbot.core.storage.gsheets import (
    BALANCES_FORMULAS,
    TRANSACTIONS_HEADER,
    USERS_HEADER,
    GoogleSheetsStorage,
    SetupError,
    SheetFormatError,
    parse_bool,
    parse_transaction_row,
    parse_user_row,
    setup_spreadsheet,
    transaction_to_row,
    with_retries,
)


def api_error(code: int) -> APIError:
    response = requests.Response()
    response.status_code = code
    response._content = json.dumps({"error": {"code": code, "message": "x", "status": "X"}}).encode()
    return APIError(response)


class FakeWorksheet:
    _next_id = 0

    def __init__(self, title: str, rows: list[list] | None = None):
        FakeWorksheet._next_id += 1
        self.id = FakeWorksheet._next_id
        self.title = title
        self.rows = rows or []
        self.updates: list[tuple] = []
        self.fail_appends: list[tuple[int, bool]] = []  # (status, write_before_failing)

    def get_all_values(self, value_render_option=None):
        return [list(r) for r in self.rows]

    def col_values(self, col):
        return [r[col - 1] if len(r) >= col else "" for r in self.rows]

    def row_values(self, row):
        return list(self.rows[row - 1]) if len(self.rows) >= row else []

    def append_row(self, values, **kwargs):
        if self.fail_appends:
            status, write = self.fail_appends.pop(0)
            if write:
                self.rows.append(list(values))
            raise api_error(status)
        self.rows.append(list(values))

    def update(self, values, range_name=None, **kwargs):
        self.updates.append((range_name, values, kwargs.get("value_input_option")))
        if range_name == "A1":
            self.rows[:1] = [list(values[0])]

    def update_title(self, title):
        self.title = title

    def freeze(self, rows=None, cols=None):
        pass

    def format(self, ranges, fmt):
        pass


class FakeSpreadsheet:
    def __init__(self, sheets: list[FakeWorksheet]):
        self.sheets = sheets
        self.batches: list[dict] = []
        self.protected: dict[int, list] = {}

    def worksheets(self):
        return list(self.sheets)

    def worksheet(self, title):
        for ws in self.sheets:
            if ws.title == title:
                return ws
        raise WorksheetNotFound(title)

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet(title)
        self.sheets.append(ws)
        return ws

    def batch_update(self, body):
        self.batches.append(body)
        for req in body["requests"]:
            if "addProtectedRange" in req:
                pr = req["addProtectedRange"]["protectedRange"]
                self.protected.setdefault(pr["range"]["sheetId"], []).append(pr)

    def list_protected_ranges(self, sheet_id):
        return self.protected.get(sheet_id, [])


def make_tx(tx_id="t1", amount=10, note="") -> Transaction:
    return Transaction(tx_id, datetime(2026, 10, 6, 9, 30, tzinfo=timezone.utc), "deduct", "telegram:2", "Oper",
                       "Mia", TxType.DEDUCT, amount, note)


@pytest.fixture
def book() -> FakeSpreadsheet:
    users = FakeWorksheet("Users", [
        USERS_HEADER,
        [1, "Admin", "admin", False, True, ""],
        [2, "Oper", "Operator", "", "", "role case is ignored"],
        ["", "Mia", "member", "", True, "no telegram"],
        ["", "", "", "", "", ""],                       # blank row
        [3, "Bad", "boss", False, True, "invalid role"],  # skipped with a warning
    ])
    txs = FakeWorksheet("Transactions", [
        TRANSACTIONS_HEADER,
        ["seed", "2026-10-01 09:00:00", "points", 1, "Admin", "Mia", "award", 50, "Opening"],
    ])
    return FakeSpreadsheet([users, txs, FakeWorksheet("Balances", [["name"]])])


@pytest.fixture
def gstorage(book) -> GoogleSheetsStorage:
    return GoogleSheetsStorage(book, sleep=lambda s: None)


def test_parse_user_rows(gstorage):
    users = gstorage.list_users()
    assert [u.name for u in users] == ["Admin", "Oper", "Mia"]
    assert users[1].role is Role.OPERATOR and users[1].active and not users[1].allow_negative
    assert users[2].ids == {}
    assert users[0].ids == {"telegram": "1"}
    assert any("row 6" in w for w in gstorage.warnings)


@pytest.mark.parametrize("value, expected", [(True, True), ("TRUE", True), ("x", True), ("", False),
                                             ("no", False), (False, False)])
def test_parse_bool(value, expected):
    assert parse_bool(value, default=False) is expected


def test_parse_user_row_ids():
    with pytest.raises(SheetFormatError):
        parse_user_row([True, "Mia", "member"])
    assert parse_user_row([123456789.0, "Mia", "member"]).ids == {"telegram": "123456789"}


def test_users_are_read_by_column_name_and_extra_platform_columns_work(book):
    users_ws = book.worksheet("Users")
    users_ws.rows = [
        ["Name", "Role", "Telegram_ID", "discord_id", "slack_id", "active"],
        ["Oper", "operator", 2, "8035111022", "", True],
        ["Sam", "member", "", "", "U0SAM", True],
    ]
    storage = GoogleSheetsStorage(book)
    oper, sam = storage.list_users()
    assert oper.ids == {"telegram": "2", "discord": "8035111022"}
    assert sam.ids == {"slack": "U0SAM"}
    service = PointsService(storage)
    assert service.authorize(Identity("discord", "8035111022")).name == "Oper"


def test_users_tab_without_required_columns_is_reported(book):
    book.worksheet("Users").rows = [["telegram_id", "who"], [1, "Anna"]]
    with pytest.raises(SheetFormatError, match="name, role"):
        GoogleSheetsStorage(book).list_users()


def test_transaction_row_roundtrip():
    tx = make_tx(note="=HYPERLINK(\"http://evil\")")
    row = transaction_to_row(tx)
    assert row[1] == "2026-10-06 09:30:00" and row[7] == 10
    assert parse_transaction_row(row) == tx


@pytest.mark.parametrize("row", [
    ["t", "2026-10-06 09:30:00", "deduct", 2, "Oper", "Mia", "steal", 10, ""],
    ["t", "2026-10-06 09:30:00", "deduct", 2, "Oper", "Mia", "deduct", -10, ""],
    ["t", "yesterday", "deduct", 2, "Oper", "Mia", "deduct", 10, ""],
])
def test_broken_transaction_rows_are_reported(row):
    with pytest.raises(SheetFormatError):
        parse_transaction_row(row)


def test_append_and_read_back(gstorage, book):
    gstorage.append_transaction(make_tx())
    assert gstorage.transaction_exists("t1")
    assert [t.tx_id for t in gstorage.list_transactions()] == ["seed", "t1"]


def test_append_retry_does_not_duplicate_when_first_attempt_landed(gstorage, book):
    txs = book.worksheet("Transactions")
    txs.fail_appends = [(503, True)]  # the row was written, but the response was an error
    gstorage.append_transaction(make_tx())
    assert [r[0] for r in txs.rows].count("t1") == 1


def test_append_retries_transient_errors(gstorage, book):
    txs = book.worksheet("Transactions")
    txs.fail_appends = [(429, False), (500, False)]
    gstorage.append_transaction(make_tx())
    assert [r[0] for r in txs.rows].count("t1") == 1


def test_append_does_not_retry_permission_errors(gstorage, book):
    book.worksheet("Transactions").fail_appends = [(403, False)]
    with pytest.raises(APIError):
        gstorage.append_transaction(make_tx())


def test_with_retries_gives_up_after_attempts():
    calls = []

    def always_fails():
        calls.append(1)
        raise api_error(503)

    with pytest.raises(APIError):
        with_retries(always_fails, attempts=3, sleep=lambda s: None)
    assert len(calls) == 3


def test_users_are_cached_briefly(book):
    clock = [0.0]
    storage = GoogleSheetsStorage(book, users_cache_seconds=30, monotonic=lambda: clock[0])
    assert len(storage.list_users()) == 3
    book.worksheet("Users").rows.append([9, "New", "member", False, True, ""])
    assert len(storage.list_users()) == 3
    clock[0] = 31
    assert len(storage.list_users()) == 4


def test_service_works_on_sheets_storage(gstorage):
    service = PointsService(gstorage)
    oper = Identity("telegram", "2")
    service.prepare(bot=DEDUCT_BOT, operator=oper, tx_type=TxType.DEDUCT, target_query="mia",
                    amount=5, note="", tx_id="x1")
    assert service.confirm("x1", oper).balance_after == 45
    assert service.confirm("x1", oper).status is CommitStatus.DUPLICATE
    assert gstorage.list_transactions()[-1].operator_id == "telegram:2"


def test_setup_on_new_spreadsheet_creates_tabs_and_admin():
    book = FakeSpreadsheet([FakeWorksheet("Sheet1")])
    steps = setup_spreadsheet(book, admin=(555, "Paulius"))
    assert [ws.title for ws in book.sheets] == ["Users", "Transactions", "Balances"]
    assert book.worksheet("Users").rows == [USERS_HEADER, [555, "Paulius", "admin", False, True, "created by setup"]]
    assert book.worksheet("Transactions").rows == [TRANSACTIONS_HEADER]
    assert book.worksheet("Balances").updates[-1] == ("A2", [BALANCES_FORMULAS], "USER_ENTERED")
    assert any("first admin" in s for s in steps)

    # Running it again changes nothing important and adds no second protection.
    setup_spreadsheet(book, admin=(555, "Paulius"))
    assert len(book.worksheet("Users").rows) == 2
    assert all(len(v) == 1 for v in book.protected.values()) and len(book.protected) == 2


def test_setup_refuses_foreign_headers():
    book = FakeSpreadsheet([FakeWorksheet("Users", [["id", "who"]])])
    with pytest.raises(SetupError):
        setup_spreadsheet(book)
