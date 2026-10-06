"""The adapter contract: IncomingCommand / IncomingAction -> Response, plus the console adapter."""

from __future__ import annotations

import pytest

from pointsbot.adapters.console import make_session, run_demo
from pointsbot.core import (
    DEDUCT_BOT,
    POINTS_BOT,
    CommandProcessor,
    IncomingAction,
    IncomingCommand,
    Style,
    TxType,
    parse_command_text,
)
from pointsbot.core.commands import UsageError, parse_operation_args
from pointsbot.core.storage.memory import DEMO_ADMIN, DEMO_MEMBER, DEMO_OPERATOR, DEMO_STRANGER

from .conftest import ADMIN, OPERATOR, OPERATOR_ON_DISCORD, STRANGER


def command(sender, text, event_id="e1"):
    name, _mention, args = parse_command_text(text)
    return IncomingCommand(sender=sender, event_id=event_id, command=name, args=args)


def test_parse_operation_args_with_multiword_name_and_note():
    args = parse_operation_args("Mia Smith 15 for the great demo", TxType.AWARD)
    assert (args.person, args.amount, args.note) == ("Mia Smith", 15, "for the great demo")


@pytest.mark.parametrize("raw", ["", "Mia", "10", "Mia 2.5", "Mia -3"])
def test_parse_operation_args_rejects_bad_input(raw):
    with pytest.raises(UsageError):
        parse_operation_args(raw, TxType.DEDUCT)


def test_parse_command_text():
    assert parse_command_text("/Award@PointsBot Mia 5") == ("award", "PointsBot", "Mia 5")
    assert parse_command_text("/help") == ("help", None, "")
    assert parse_command_text("hello") is None
    assert parse_command_text("/") is None


def test_stranger_gets_polite_refusal_with_their_platform_id(service):
    response = CommandProcessor(service, POINTS_BOT).handle_command(command(STRANGER, "/deduct Mia 5"))
    assert "Sorry" in response.plain and "Telegram ID is 999" in response.plain and not response.buttons


def test_full_flow(service, storage):
    processor = CommandProcessor(service, DEDUCT_BOT)
    response = processor.handle_command(command(OPERATOR, "/deduct Mia 10 late", event_id="tg-u2-10"))
    assert response.plain.startswith("Deduct 10 points from Mia?")
    yes = response.buttons[0]
    assert yes.token == "deduct-tg-u2-10"
    assert "Done" in processor.handle_action(IncomingAction(OPERATOR, yes.action, yes.token)).plain
    again = processor.handle_action(IncomingAction(OPERATOR, yes.action, yes.token))
    assert "already recorded" in again.plain
    assert len(storage.transactions) == 3


def test_responses_are_rich_text_not_markup(service):
    processor = CommandProcessor(service, POINTS_BOT)
    response = processor.handle_command(command(OPERATOR, "/award Mia 1 <b>hi</b> *x*"))
    assert "<b>hi</b> *x*" in response.plain  # user text is kept verbatim, adapters escape it
    assert any(span.style is Style.BOLD and span.text == "Mia" for span in response.text)


def test_someone_else_pressing_the_button_gets_a_popup(service):
    processor = CommandProcessor(service, POINTS_BOT)
    response = processor.handle_command(command(OPERATOR, "/award Mia 1"))
    other = processor.handle_action(IncomingAction(ADMIN, "yes", response.buttons[0].token))
    assert other.alert_only


def test_myid_and_help_mention_the_senders_platform(service):
    processor = CommandProcessor(service, POINTS_BOT)
    assert processor.handle_command(command(OPERATOR_ON_DISCORD, "/myid")).plain == "Your Discord ID is 8035111022."
    assert "your Discord ID" in processor.handle_command(command(OPERATOR_ON_DISCORD, "/help")).plain


def test_unexpected_storage_error_is_reported_politely(service, storage, monkeypatch):
    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(storage, "list_transactions", broken)
    response = CommandProcessor(service, POINTS_BOT).handle_command(command(ADMIN, "/balance"))
    assert "Something went wrong" in response.plain


def test_console_retry_and_double_tap():
    s = make_session()
    s.bot, s.sender = "deduct", DEMO_OPERATOR
    assert "Deduct" in s.send("/deduct Mia 10")
    assert "Done" in s.execute(":yes")
    assert "already recorded" in s.execute(":tap")
    assert "already processed" in s.execute(":retry")
    s.bot, s.sender = "points", DEMO_ADMIN
    assert "Mia" in s.execute("/balance") and "40" in s.execute("/balance")


def test_console_member_stranger_and_identity_switch():
    s = make_session()
    s.sender = DEMO_MEMBER
    assert "Your balance: 50 points" in s.execute("/balance")
    s.sender = DEMO_STRANGER
    assert "don't have access" in s.execute("/help")
    assert s.execute(":as 1001") == "Now acting as telegram:1001."
    assert s.execute(":as discord:42") == "Now acting as discord:42."
    assert "Discord ID is 42" in s.execute("/balance")


def test_scripted_demo_runs_and_records_expected_rows(capsys):
    session = run_demo()
    out = capsys.readouterr().out
    assert "Traceback" not in out and "Something went wrong" not in out
    assert len(session.service.storage.list_transactions()) == 5  # 3 seed rows + 2 confirmed
