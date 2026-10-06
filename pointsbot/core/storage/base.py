"""Storage interface. The service only talks to this protocol."""

from __future__ import annotations

from typing import Protocol

from ..models import Transaction, User


class Storage(Protocol):
    def list_users(self) -> list[User]:
        """All rows of the "Users" sheet (active and inactive)."""

    def list_transactions(self) -> list[Transaction]:
        """All rows of the "Transactions" sheet, oldest first."""

    def transaction_exists(self, tx_id: str) -> bool:
        """True if a transaction with this ID is already stored."""

    def append_transaction(self, tx: Transaction) -> None:
        """Append one row. Must never modify existing rows."""
