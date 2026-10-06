"""In-memory storage used by MOCK mode and by the unit tests."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import datetime, timezone

from ..models import Role, Transaction, TxType, User


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


# Fake Telegram IDs used by the demo data and the mock console.
DEMO_ADMIN_ID = 1001
DEMO_OPERATOR_ID = 1002
DEMO_MEMBER_ID = 1003
DEMO_STRANGER_ID = 9999


def demo_storage() -> InMemoryStorage:
    """A small, realistic data set for the mock console and the demo script."""
    users = [
        User("Anna", Role.ADMIN, DEMO_ADMIN_ID),
        User("Oscar", Role.OPERATOR, DEMO_OPERATOR_ID),
        User("Mia", Role.MEMBER, DEMO_MEMBER_ID),
        User("Leo", Role.MEMBER),  # no Telegram account: can hold points only
        User("Nora", Role.MEMBER, 1004, allow_negative=True),
    ]
    start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    seed = [("Mia", 50), ("Leo", 20), ("Nora", 10)]
    transactions = [
        Transaction(
            tx_id=f"seed-{i}",
            timestamp=start,
            bot="points",
            operator_id=DEMO_ADMIN_ID,
            operator_name="Anna",
            target=name,
            type=TxType.AWARD,
            amount=amount,
            note="Opening balance",
        )
        for i, (name, amount) in enumerate(seed, start=1)
    ]
    return InMemoryStorage(users, transactions)
