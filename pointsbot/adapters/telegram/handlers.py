"""aiogram handlers: translate Telegram updates to core calls and back.

No business rules live here: everything is decided by `CommandProcessor`.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ...core import CommandProcessor, Identity, IncomingAction, IncomingCommand, Response, parse_command_text
from .render import to_html

log = logging.getLogger(__name__)

PLATFORM = "telegram"


class ButtonCallback(CallbackData, prefix="tx"):
    """Callback data of the confirmation buttons: "tx:<action>:<token>" (well under 64 bytes)."""

    action: str
    token: str


def identity(user_id: int) -> Identity:
    return Identity(PLATFORM, str(user_id))


def event_id(chat_id: int, message_id: int) -> str:
    """Stable per message: Telegram re-delivers an update with the same chat and message ID."""
    chat = f"g{-chat_id}" if chat_id < 0 else f"u{chat_id}"
    return f"tg-{chat}-{message_id}"


def keyboard(response: Response) -> InlineKeyboardMarkup | None:
    if not response.buttons:
        return None
    row = [
        InlineKeyboardButton(text=b.text, callback_data=ButtonCallback(action=b.action, token=b.token).pack())
        for b in response.buttons
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])


async def on_command(message: Message, bot: Bot, processors: dict[int, CommandProcessor], usernames: dict[int, str]):
    if message.from_user is None or message.from_user.is_bot:
        return
    parsed = parse_command_text(message.text or "")
    if parsed is None:
        return
    command, mention, args = parsed
    username = usernames.get(bot.id)
    if mention and username and mention.lower() != username.lower():
        return  # addressed to another bot in a group chat
    incoming = IncomingCommand(
        sender=identity(message.from_user.id),
        event_id=event_id(message.chat.id, message.message_id),
        command=command,
        args=args,
    )
    # The core does blocking Google API calls, so keep them off the event loop.
    response = await asyncio.to_thread(processors[bot.id].handle_command, incoming)
    await message.answer(to_html(response.text), reply_markup=keyboard(response))


async def on_other_message(message: Message):
    await message.answer("I only understand commands. Send /help to see them.")


async def on_button(
    query: CallbackQuery, callback_data: ButtonCallback, bot: Bot, processors: dict[int, CommandProcessor]
):
    incoming = IncomingAction(
        sender=identity(query.from_user.id), action=callback_data.action, token=callback_data.token
    )
    response = await asyncio.to_thread(processors[bot.id].handle_action, incoming)
    if response.alert_only or query.message is None:
        await query.answer(response.plain[:200], show_alert=True)  # Telegram pop-up limit
        return
    await query.answer()
    try:
        # Replace the question with the outcome and remove the buttons, so the
        # same message cannot be confirmed twice from the UI.
        await query.message.edit_text(to_html(response.text), reply_markup=None)
    except TelegramBadRequest as exc:  # e.g. "message is not modified" on a double tap
        log.debug("edit_text skipped: %s", exc)


def create_router() -> Router:
    """A fresh router (a router can be attached to only one dispatcher)."""
    router = Router(name="points")
    router.message.register(on_command, F.text.startswith("/"))
    router.message.register(on_other_message, F.chat.type == "private")
    router.callback_query.register(on_button, ButtonCallback.filter())
    return router
