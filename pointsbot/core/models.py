"""Domain models shared by the core and every adapter.

Nothing here knows about Telegram (or any other messaging platform). A person
is identified on a platform by an `Identity` such as ("telegram", "123456789")
or ("discord", "80351110224678912"); the "Users" sheet holds one `<platform>_id`
column per platform.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Role(str, Enum):
    """Role of a person listed in the "Users" sheet."""

    ADMIN = "admin"  # everything, sees all balances, may allow negative balances
    OPERATOR = "operator"  # may award/deduct (as far as the bot allows)
    MEMBER = "member"  # may only view own balance and history

    @classmethod
    def parse(cls, value: object) -> Role:
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            raise ValueError(f"unknown role {value!r} (expected admin, operator or member)") from exc


class TxType(str, Enum):
    AWARD = "award"
    DEDUCT = "deduct"


def normalize_name(name: str) -> str:
    """Canonical form used to compare person names ("  Anna  K " == "anna k")."""
    return " ".join(name.split()).casefold()


@dataclass(frozen=True)
class Identity:
    """Who sent a message, as seen by one messaging platform."""

    platform: str  # "telegram", "discord", "slack", "whatsapp", ...
    user_id: str  # the platform's user ID, always as a string

    def __str__(self) -> str:
        return f"{self.platform}:{self.user_id}"


@dataclass(frozen=True)
class User:
    """A person from the "Users" sheet.

    `ids` maps platform -> user ID. A person without any ID can hold points
    but cannot use the bots.
    """

    name: str
    role: Role
    ids: Mapping[str, str] = field(default_factory=dict)
    allow_negative: bool = False
    active: bool = True

    @property
    def key(self) -> str:
        return normalize_name(self.name)

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    def id_on(self, platform: str) -> str | None:
        return self.ids.get(platform)

    def matches(self, identity: Identity) -> bool:
        return self.ids.get(identity.platform) == identity.user_id


@dataclass(frozen=True)
class Transaction:
    """One immutable row of the append-only "Transactions" sheet."""

    tx_id: str
    timestamp: datetime  # timezone-aware, UTC
    bot: str
    operator_id: str  # Identity as "platform:user_id"
    operator_name: str
    target: str
    type: TxType
    amount: int  # always positive; the sign comes from `type`
    note: str = ""

    @property
    def signed_amount(self) -> int:
        return self.amount if self.type is TxType.AWARD else -self.amount


@dataclass(frozen=True)
class BotProfile:
    """What one bot is allowed to do, independent of the platform it runs on."""

    key: str  # short id stored in the sheet and used in transaction IDs
    title: str
    allowed_types: frozenset[TxType]


DEDUCT_BOT = BotProfile(key="deduct", title="Deduct bot", allowed_types=frozenset({TxType.DEDUCT}))
POINTS_BOT = BotProfile(key="points", title="Points bot", allowed_types=frozenset({TxType.AWARD, TxType.DEDUCT}))
BOT_PROFILES = {p.key: p for p in (DEDUCT_BOT, POINTS_BOT)}
