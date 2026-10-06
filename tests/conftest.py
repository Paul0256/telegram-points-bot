from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pointsbot.core.models import Identity, Role, Transaction, TxType, User
from pointsbot.core.service import PointsService
from pointsbot.core.storage.memory import InMemoryStorage

ADMIN, OPERATOR, MEMBER, INACTIVE, STRANGER = (Identity("telegram", str(i)) for i in (1, 2, 3, 4, 999))
OPERATOR_ON_DISCORD = Identity("discord", "8035111022")


def tg(identity: Identity) -> dict[str, str]:
    return {"telegram": identity.user_id}


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def award(target: str, amount: int, tx_id: str) -> Transaction:
    return Transaction(
        tx_id=tx_id,
        timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
        bot="points",
        operator_id=str(ADMIN),
        operator_name="Admin",
        target=target,
        type=TxType.AWARD,
        amount=amount,
    )


@pytest.fixture
def users() -> list[User]:
    return [
        User("Admin", Role.ADMIN, tg(ADMIN)),
        User("Oper", Role.OPERATOR, {"telegram": OPERATOR.user_id, "discord": OPERATOR_ON_DISCORD.user_id}),
        User("Mia", Role.MEMBER, tg(MEMBER)),
        User("Gone", Role.OPERATOR, tg(INACTIVE), active=False),
        User("Leo", Role.MEMBER),  # no messaging account
        User("Nora", Role.MEMBER, {"telegram": "5"}, allow_negative=True),
    ]


@pytest.fixture
def storage(users: list[User]) -> InMemoryStorage:
    return InMemoryStorage(users, [award("Mia", 50, "seed-1"), award("Leo", 20, "seed-2")])


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def service(storage: InMemoryStorage, clock: FakeClock) -> PointsService:
    return PointsService(storage, clock=clock, max_amount=1000)
