"""Telegram adapter tested through aiogram's real dispatcher with a fake HTTP session."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

from pointsbot.adapters.telegram.app import bot_commands, build_dispatcher
from pointsbot.adapters.telegram.handlers import ButtonCallback
from pointsbot.core import DEDUCT_BOT, CommandProcessor

from .conftest import OPERATOR, STRANGER


class FakeSession(BaseSession):
    """Records every Bot API call instead of sending it."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(message_id=500, date=datetime.now(), chat=Chat(id=1, type="private"), text=method.text)
        return True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover - not used
        raise NotImplementedError

    async def close(self) -> None:
        pass


def tg_user(user_id: int | str) -> TgUser:
    return TgUser(id=int(user_id), is_bot=False, first_name=f"user{user_id}")


def message_update(update_id: int, sender, text: str, message_id: int, chat_id: int | None = None) -> Update:
    chat = Chat(id=chat_id or int(sender.user_id), type="private" if chat_id is None else "group")
    return Update(update_id=update_id, message=Message(
        message_id=message_id, date=datetime.now(), chat=chat, from_user=tg_user(sender.user_id), text=text))


def callback_update(update_id: int, sender, data: str) -> Update:
    chat = Chat(id=int(sender.user_id), type="private")
    return Update(update_id=update_id, callback_query=CallbackQuery(
        id=str(update_id), from_user=tg_user(sender.user_id), chat_instance="ci", data=data,
        message=Message(message_id=501, date=datetime.now(), chat=chat, text="...")))


@pytest.fixture
def setup(service):
    bot = Bot("1001001:TEST-token_for_unit_tests", session=FakeSession())
    dispatcher = build_dispatcher({bot.id: CommandProcessor(service, DEDUCT_BOT)}, {bot.id: "deduct_test_bot"})
    return bot, dispatcher


def run(coro):
    return asyncio.run(coro)


def test_deduct_confirm_and_double_tap(setup, storage):
    bot, dp = setup
    calls = bot.session.calls

    run(dp.feed_update(bot, message_update(1, OPERATOR, "/deduct Mia 10 late", message_id=42)))
    sent = calls[-1]
    assert isinstance(sent, SendMessage) and "Deduct <b>10 points</b>" in sent.text
    yes_data = sent.reply_markup.inline_keyboard[0][0].callback_data
    assert ButtonCallback.unpack(yes_data).token == "deduct-tg-u2-42"

    run(dp.feed_update(bot, callback_update(2, OPERATOR, yes_data)))
    edits = [c for c in calls if isinstance(c, EditMessageText)]
    assert "Done" in edits[-1].text and edits[-1].reply_markup is None

    run(dp.feed_update(bot, callback_update(3, OPERATOR, yes_data)))  # double tap
    assert "already recorded" in [c for c in calls if isinstance(c, EditMessageText)][-1].text
    assert sum(t.tx_id == "deduct-tg-u2-42" for t in storage.transactions) == 1
    assert storage.transactions[-1].operator_id == "telegram:2"


def test_redelivered_update_does_not_create_second_confirmation_row(setup, storage):
    bot, dp = setup
    update = message_update(1, OPERATOR, "/deduct Mia 10", message_id=7)
    run(dp.feed_update(bot, update))
    data = bot.session.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    run(dp.feed_update(bot, callback_update(2, OPERATOR, data)))
    run(dp.feed_update(bot, update))  # Telegram retry of the original command
    assert "already processed" in bot.session.calls[-1].text
    assert len(storage.transactions) == 3


def test_stranger_is_refused(setup):
    bot, dp = setup
    run(dp.feed_update(bot, message_update(1, STRANGER, "/help", message_id=1)))
    assert "don't have access" in bot.session.calls[-1].text


def test_foreign_button_press_shows_alert(setup):
    bot, dp = setup
    run(dp.feed_update(bot, message_update(1, OPERATOR, "/deduct Mia 1", message_id=3)))
    data = bot.session.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    run(dp.feed_update(bot, callback_update(2, STRANGER, data)))
    answer = bot.session.calls[-1]
    assert isinstance(answer, AnswerCallbackQuery) and answer.show_alert


def test_plain_text_gets_a_hint(setup):
    bot, dp = setup
    run(dp.feed_update(bot, message_update(1, OPERATOR, "hello", message_id=1)))
    assert "/help" in bot.session.calls[-1].text


def test_deduct_bot_menu_has_no_award():
    assert "award" not in [c.command for c in bot_commands(DEDUCT_BOT)]


def test_user_text_is_html_escaped(setup):
    bot, dp = setup
    run(dp.feed_update(bot, message_update(1, OPERATOR, "/deduct Mia 1 <script>&", message_id=9)))
    text = bot.session.calls[-1].text
    assert "Note: &lt;script&gt;&amp;" in text and "<script>" not in text


def test_command_for_another_bot_in_a_group_is_ignored(setup):
    bot, dp = setup
    run(dp.feed_update(bot, message_update(1, OPERATOR, "/help@other_bot", message_id=1, chat_id=-100)))
    assert bot.session.calls == []
    run(dp.feed_update(bot, message_update(2, OPERATOR, "/help@deduct_test_bot", message_id=2, chat_id=-100)))
    assert "Deduct bot" in bot.session.calls[-1].text
