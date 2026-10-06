"""In-memory storage used by MOCK mode and by the unit tests."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import datetime, timezone

from ..models import Identity, Role, Transaction, TxType, User


class InMemoryStorage:
    def __init__(self, users: Iterable[User] = (), transactions: Iterable[Transaction] = ()):
        self._lock = threading.Lock()
        self.users: list[User] = list(users)
        self.transactions: list[Transaction] = list(transactions)

    def list_users(self) -> list[User]:
        with self._lock:
            return list(self.users)

    def list_transactions(self) -> list[Transaction]:
        with self._lock:
            return list(self.transactions)

    def transaction_exists(self, tx_id: str) -> bool:
        with self._lock:
            return any(t.tx_id == tx_id for t in self.transactions)

    def append_transaction(self, tx: Transaction) -> None:
        with self._lock:
            self.transactions.append(tx)


# Fake Telegram identities used by the demo data and the console adapter.
DEMO_PLATFORM = "telegram"
DEMO_ADMIN = Identity(DEMO_PLATFORM, "1001")
DEMO_OPERATOR = Identity(DEMO_PLATFORM, "1002")
DEMO_MEMBER = Identity(DEMO_PLATFORM, "1003")
DEMO_STRANGER = Identity(DEMO_PLATFORM, "9999")


def demo_storage() -> InMemoryStorage:
    """A small, realistic data set for the mock console and the demo script."""
    users = [
        User("Anna", Role.ADMIN, {"telegram": DEMO_ADMIN.user_id}),
        User("Oscar", Role.OPERATOR, {"telegram": DEMO_OPERATOR.user_id}),
        User("Mia", Role.MEMBER, {"telegram": DEMO_MEMBER.user_id}),
        User("Leo", Role.MEMBER),  # no messaging account: can hold points only
        User("Nora", Role.MEMBER, {"telegram": "1004"}, allow_negative=True),
    ]
    start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    seed = [("Mia", 50), ("Leo", 20), ("Nora", 10)]
    transactions = [
        Transaction(
            tx_id=f"seed-{i}",
            timestamp=start,
            bot="points",
            operator_id=str(DEMO_ADMIN),
            operator_name="Anna",
            target=name,
            type=TxType.AWARD,
            amount=amount,
            note="Opening balance",
        )
        for i, (name, amount) in enumerate(seed, start=1)
    ]
    return InMemoryStorage(users, transactions)
