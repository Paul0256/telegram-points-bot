"""Permission rules in one place, so they are easy to review and test.

Matrix:

    action                     member  operator  admin
    use the bots at all          yes      yes      yes   (needs a platform ID + active)
    award / deduct               -        yes      yes   (only types the bot allows)
    view own balance/history     yes      yes      yes
    view everyone                -        -        yes
    push a balance below zero    -        -        yes   (explicit confirmation)
"""

from __future__ import annotations

from .models import BotProfile, Role, TxType, User

OPERATING_ROLES = frozenset({Role.ADMIN, Role.OPERATOR})


def can_use_bots(user: User | None) -> bool:
    return user is not None and user.active and any(user.ids.values())


def bot_supports(bot: BotProfile, tx_type: TxType) -> bool:
    return tx_type in bot.allowed_types


def can_operate(user: User | None, bot: BotProfile, tx_type: TxType) -> bool:
    return can_use_bots(user) and user.role in OPERATING_ROLES and bot_supports(bot, tx_type)


def can_view_all(user: User | None) -> bool:
    return can_use_bots(user) and user.role is Role.ADMIN


def can_override_negative(user: User | None) -> bool:
    return can_use_bots(user) and user.role is Role.ADMIN
