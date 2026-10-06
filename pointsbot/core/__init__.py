"""Platform-independent core: permissions, points logic, duplicate protection, storage.

Rule: nothing in this package may import a messaging library (aiogram, discord.py,
slack_sdk, ...) or anything from `pointsbot.adapters`. `tests/test_architecture.py`
enforces it.

Adapters only need this public API:

    processor = CommandProcessor(PointsService(storage), POINTS_BOT)
    response = processor.handle_command(IncomingCommand(sender, event_id, "deduct", "Mia 10"))
    response = processor.handle_action(IncomingAction(sender, button.action, button.token))
"""

from .commands import CANCEL, CONFIRM, OVERRIDE, CommandProcessor
from .messages import (
    Button,
    IncomingAction,
    IncomingCommand,
    Response,
    RichText,
    Span,
    Style,
    parse_command_text,
    render_plain,
)
from .models import BOT_PROFILES, DEDUCT_BOT, POINTS_BOT, BotProfile, Identity, Role, Transaction, TxType, User
from .service import PointsService

__all__ = [
    "BOT_PROFILES", "CANCEL", "CONFIRM", "DEDUCT_BOT", "OVERRIDE", "POINTS_BOT", "BotProfile", "Button",
    "CommandProcessor", "Identity", "IncomingAction", "IncomingCommand", "PointsService", "Response", "RichText",
    "Role", "Span", "Style", "Transaction", "TxType", "User", "parse_command_text", "render_plain",
]  # fmt: skip
