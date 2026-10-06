"""Run both bots in one process, sharing one PointsService.

One process (instead of two) means one in-memory lock guards every write, so
the two bots can never race each other on the same balance.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from ..commands import CommandHandler
from ..config import Settings
from ..models import DEDUCT_BOT, POINTS_BOT, BotProfile, TxType
from ..service import PointsService
from .handlers import create_router

log = logging.getLogger(__name__)


def bot_commands(profile: BotProfile) -> list[BotCommand]:
    commands = []
    if TxType.AWARD in profile.allowed_types:
        commands.append(BotCommand(command="award", description="Award points: /award <name> <amount> [note]"))
    commands += [
        BotCommand(command="deduct", description="Deduct points: /deduct <name> <amount> [note]"),
        BotCommand(command="balance", description="Show balance"),
        BotCommand(command="history", description="Last 10 transactions"),
        BotCommand(command="myid", description="Show my Telegram ID"),
        BotCommand(command="help", description="How to use this bot"),
    ]
    return commands


def build_dispatcher(command_handlers: dict[int, CommandHandler], usernames: dict[int, str]) -> Dispatcher:
    """Dispatcher shared by both bots; handlers pick their profile by `bot.id`."""
    dispatcher = Dispatcher(command_handlers=command_handlers, usernames=usernames)
    dispatcher.include_router(create_router())
    return dispatcher


async def run_bots(settings: Settings, service: PointsService) -> None:
    default = DefaultBotProperties(parse_mode=ParseMode.HTML)
    pairs = [
        (Bot(settings.deduct_bot_token, default=default), DEDUCT_BOT),
        (Bot(settings.points_bot_token, default=default), POINTS_BOT),
    ]
    command_handlers: dict[int, CommandHandler] = {}
    usernames: dict[int, str] = {}
    for bot, profile in pairs:
        me = await bot.get_me()  # also validates the token
        command_handlers[bot.id] = CommandHandler(service, profile, settings.timezone)
        usernames[bot.id] = me.username or ""
        await bot.set_my_commands(bot_commands(profile))
        log.info("%s connected as @%s", profile.title, me.username)

    dispatcher = build_dispatcher(command_handlers, usernames)
    bots = [bot for bot, _ in pairs]
    try:
        # Pending updates are NOT dropped: anything sent while the bot was down
        # is processed, and duplicates are filtered by transaction ID.
        await dispatcher.start_polling(*bots, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        for bot in bots:
            await bot.session.close()
