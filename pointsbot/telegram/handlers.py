"""aiogram handlers: a thin adapter between Telegram and `CommandHandler`."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..commands import CommandHandler, Reply

log = logging.getLogger(__name__)


class TxCallback(CallbackData, prefix="tx"):
    """Callback data of the confirmation buttons: "tx:<action>:<tx_id>" (well under 64 bytes)."""

    action: str
    tx_id: str


def keyboard(reply: Reply) -> InlineKeyboardMarkup | None:
    if not reply.buttons:
        return None
    row = [
        InlineKeyboardButton(text=b.text, callback_data=TxCallback(action=b.action, tx_id=b.tx_id).pack())
        for b in reply.buttons
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def on_command(
    message: Message, bot: Bot, command_handlers: dict[int, CommandHandler], usernames: dict[int, str]
):
    if message.from_user is None or message.from_user.is_bot:
        return
    handler = command_handlers[bot.id]
    # The service does blocking Google API calls, so keep them off the event loop.
    reply = await asyncio.to_thread(
        handler.handle_command,
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        text=message.text,
        bot_username=usernames.get(bot.id),
    )
    if reply is not None:
        await message.answer(reply.text, reply_markup=keyboard(reply))


async def on_other_message(message: Message):
    await message.answer("I only understand commands. Send /help to see them.")


async def on_button(
    query: CallbackQuery, callback_data: TxCallback, bot: Bot, command_handlers: dict[int, CommandHandler]
):
    handler = command_handlers[bot.id]
    reply = await asyncio.to_thread(
        handler.handle_button, user_id=query.from_user.id, action=callback_data.action, tx_id=callback_data.tx_id
    )
    if reply.alert_only or query.message is None:
        await query.answer(reply.text, show_alert=True)
        return
    await query.answer()
    try:
        # Replace the question with the outcome and remove the buttons, so the
        # same message cannot be confirmed twice from the UI.
        await query.message.edit_text(reply.text, reply_markup=None)
    except TelegramBadRequest as exc:  # e.g. "message is not modified" on a double tap
        log.debug("edit_text skipped: %s", exc)


def create_router() -> Router:
    """A fresh router (a router can be attached to only one dispatcher)."""
    router = Router(name="points")
    router.message.register(on_command, F.text.startswith("/"))
    router.message.register(on_other_message, F.chat.type == "private")
    router.callback_query.register(on_button, TxCallback.filter())
    return router
