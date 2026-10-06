"""End-to-end tests through the transport-agnostic command layer (MOCK mode)."""

from __future__ import annotations

import pytest

from pointsbot.commands import CommandHandler, UsageError, parse_operation_args, split_command
from pointsbot.mock_console import make_session, run_demo
from pointsbot.models import DEDUCT_BOT, POINTS_BOT, TxType
from pointsbot.storage.memory import DEMO_ADMIN_ID, DEMO_MEMBER_ID, DEMO_OPERATOR_ID, DEMO_STRANGER_ID

from .conftest import ADMIN, OPERATOR, STRANGER


def test_parse_operation_args_with_multiword_name_and_note():
    args = parse_operation_args("Mia Smith 15 for the great demo", TxType.AWARD)
    assert (args.person, args.amount, args.note) == ("Mia Smith", 15, "for the great demo")


@pytest.mark.parametrize("raw", ["", "Mia", "10", "Mia 2.5", "Mia -3"])
def test_parse_operation_args_rejects_bad_input(raw):
    with pytest.raises(UsageError):
        parse_operation_args(raw, TxType.DEDUCT)


def test_split_command_handles_bot_mentions():
    assert split_command("/Award@PointsBot Mia 5") == ("award", "PointsBot", "Mia 5")
    assert split_command("/help") == ("help", None, "")


def test_stranger_gets_polite_refusal_with_their_id(service):
    reply = CommandHandler(service, POINTS_BOT).handle_command(
        user_id=STRANGER, chat_id=STRANGER, message_id=1, text="/deduct Mia 5")
    assert "Sorry" in reply.text and str(STRANGER) in reply.text and not reply.buttons


def test_command_for_another_bot_is_ignored(service):
    handler = CommandHandler(service, POINTS_BOT)
    assert handler.handle_command(user_id=ADMIN, chat_id=-5, message_id=1,
                                  text="/help@OtherBot", bot_username="PointsBot") is None


def test_full_flow_through_handler(service, storage):
    handler = CommandHandler(service, DEDUCT_BOT)
    reply = handler.handle_command(user_id=OPERATOR, chat_id=OPERATOR, message_id=10,
                                   text="/deduct Mia 10 late")
    assert "Deduct <b>10 points</b> from <b>Mia</b>?" in reply.text
    yes = reply.buttons[0]
    assert "Done" in handler.handle_button(user_id=OPERATOR, action=yes.action, tx_id=yes.tx_id).text
    assert "already recorded" in handler.handle_button(
        user_id=OPERATOR, action=yes.action, tx_id=yes.tx_id).text
    assert len(storage.transactions) == 3


def test_someone_else_pressing_the_button_gets_a_popup(service):
    handler = CommandHandler(service, POINTS_BOT)
    reply = handler.handle_command(user_id=OPERATOR, chat_id=-77, message_id=1, text="/award Mia 1")
    other = handler.handle_button(user_id=ADMIN, action="yes", tx_id=reply.buttons[0].tx_id)
    assert other.alert_only


def test_notes_are_html_escaped(service):
    handler = CommandHandler(service, POINTS_BOT)
    reply = handler.handle_command(user_id=OPERATOR, chat_id=OPERATOR, message_id=1,
                                   text="/award Mia 1 <script>")
    assert "&lt;script&gt;" in reply.text and "<script>" not in reply.text


def test_unexpected_storage_error_is_reported_politely(service, storage, monkeypatch):
    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(storage, "list_transactions", broken)
    reply = CommandHandler(service, POINTS_BOT).handle_command(
        user_id=ADMIN, chat_id=ADMIN, message_id=1, text="/balance")
    assert "Something went wrong" in reply.text


def test_mock_session_retry_and_double_tap():
    s = make_session()
    s.bot, s.user_id = "deduct", DEMO_OPERATOR_ID
    assert "Deduct" in s.send("/deduct Mia 10")
    assert "Done" in s.execute(":yes")
    assert "already recorded" in s.execute(":tap")
    assert "already processed" in s.execute(":retry")
    s.user_id = DEMO_ADMIN_ID
    s.bot = "points"
    assert "Mia" in s.execute("/balance") and "40" in s.execute("/balance")


def test_mock_session_member_and_stranger():
    s = make_session()
    s.user_id = DEMO_MEMBER_ID
    assert "Your balance: 50 points" in s.execute("/balance")
    s.user_id = DEMO_STRANGER_ID
    assert "don't have access" in s.execute("/help")


def test_scripted_demo_runs_and_records_expected_rows(capsys):
    session = run_demo()
    out = capsys.readouterr().out
    assert "Traceback" not in out and "Something went wrong" not in out
    rows = session.service.storage.list_transactions()
    assert len(rows) == 5  # 3 seed rows + Oscar's deduction + Anna's award
