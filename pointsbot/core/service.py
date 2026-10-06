"""Core business logic: authorization, two-step operations, balances, history.

The service knows nothing about messaging platforms: callers are identified
by an `Identity` (platform + user ID). It is synchronous, which keeps it easy
to test; async adapters call it from a worker thread.

Duplicate protection works on two levels:

1. Every operation gets a deterministic ID derived from the platform event
   that started it (bot + adapter event ID). Re-delivered events and repeated
   "Yes" taps therefore map to the same ID, and an ID that already exists in
   the "Transactions" sheet is never written again.
2. A process-wide lock serializes confirmation, so two concurrent taps cannot
   both pass the "does it exist yet?" check.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum

from . import permissions
from .models import BotProfile, Identity, Transaction, TxType, User, normalize_name
from .storage.base import Storage

log = logging.getLogger(__name__)

MAX_NOTE_LENGTH = 200


# --------------------------------------------------------------------------- errors


class PointsError(Exception):
    """Expected, user-facing error. The message is safe to show to the user."""


class AccessDenied(PointsError):
    """The sender is not an active entry in the "Users" sheet."""

    def __init__(self, identity: Identity):
        super().__init__("You don't have access to this bot.")
        self.identity = identity


class PermissionDenied(PointsError):
    """Known user, but this action is not allowed for them (or for this bot)."""


class InvalidAmount(PointsError):
    pass


class UnknownPerson(PointsError):
    def __init__(self, query: str, suggestions: Iterable[str] = ()):
        self.query = query
        self.suggestions = list(suggestions)
        super().__init__(f'No active person called "{query}" in the Users sheet.')


class AmbiguousPerson(PointsError):
    def __init__(self, query: str, candidates: Iterable[str]):
        self.query = query
        self.candidates = list(candidates)
        super().__init__(f'"{query}" matches several people: {", ".join(self.candidates)}.')


class InsufficientBalance(PointsError):
    def __init__(self, name: str, balance: int, amount: int):
        self.name, self.balance, self.amount = name, balance, amount
        super().__init__(
            f"{name} has only {balance} points, cannot deduct {amount}. "
            "Ask an administrator if the balance may go below zero."
        )


class AlreadyProcessed(PointsError):
    """The event that started this operation was already turned into a transaction."""

    def __init__(self, tx_id: str):
        self.tx_id = tx_id
        super().__init__("This command was already processed. Nothing was changed.")


class PendingNotFound(PointsError):
    def __init__(self) -> None:
        super().__init__("This confirmation has expired or was cancelled. Please run the command again.")


class NotYourOperation(PointsError):
    def __init__(self) -> None:
        super().__init__("Only the person who started this operation can confirm it.")


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class PendingOperation:
    """A validated operation that waits for the operator's Yes/No."""

    tx_id: str
    bot: BotProfile
    operator: Identity
    operator_name: str
    target: User
    type: TxType
    amount: int
    note: str
    balance_before: int
    created_at: datetime
    requires_override: bool  # admin must explicitly allow a negative balance

    @property
    def signed_amount(self) -> int:
        return self.amount if self.type is TxType.AWARD else -self.amount

    @property
    def balance_after(self) -> int:
        return self.balance_before + self.signed_amount


class CommitStatus(Enum):
    CREATED = "created"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class CommitResult:
    status: CommitStatus
    transaction: Transaction | None
    balance_after: int | None


@dataclass(frozen=True)
class BalanceReport:
    entries: list[tuple[str, int]]
    everyone: bool  # True when an admin asked for all balances


# --------------------------------------------------------------------------- helpers


def compute_balances(transactions: Iterable[Transaction]) -> dict[str, int]:
    """Balance per normalized person name. Mirrors the formulas of the "Balances" sheet."""
    balances: dict[str, int] = defaultdict(int)
    for tx in transactions:
        balances[normalize_name(tx.target)] += tx.signed_amount
    return dict(balances)


def make_tx_id(bot_key: str, event_id: str) -> str:
    """Deterministic transaction ID: bot + the adapter's stable event ID.

    The bot key is part of the ID because two bots may see events with equal
    IDs (e.g. Telegram numbers messages per chat, separately for each bot).
    """
    return f"{bot_key}-{event_id}"


def clean_note(note: str) -> str:
    note = " ".join(note.split())
    return note[:MAX_NOTE_LENGTH]


# --------------------------------------------------------------------------- service


class PointsService:
    def __init__(
        self,
        storage: Storage,
        *,
        max_amount: int = 100_000,
        pending_ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.storage = storage
        self.max_amount = max_amount
        self.pending_ttl = pending_ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, PendingOperation] = {}

    # ---- users -------------------------------------------------------------

    def find_user(self, identity: Identity) -> User | None:
        return next((u for u in self.storage.list_users() if u.matches(identity)), None)

    def authorize(self, identity: Identity) -> User:
        """Return the active user for this platform identity or raise AccessDenied."""
        user = self.find_user(identity)
        if not permissions.can_use_bots(user):
            raise AccessDenied(identity)
        return user

    def find_person(self, query: str) -> User:
        """Resolve a name typed by the operator to an active person.

        Exact (case-insensitive) match wins; otherwise a unique prefix match is
        accepted, so "/deduct ann 5" works for "Anna".
        """
        key = normalize_name(query)
        if not key:
            raise UnknownPerson(query)
        people = [u for u in self.storage.list_users() if u.active]
        exact = [u for u in people if u.key == key]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise AmbiguousPerson(query, [u.name for u in exact])
        partial = [u for u in people if u.key.startswith(key)]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise AmbiguousPerson(query, sorted(u.name for u in partial))
        suggestions = sorted(u.name for u in people if key in u.key)
        raise UnknownPerson(query, suggestions)

    # ---- two-step operations --------------------------------------------------

    def prepare(
        self,
        *,
        bot: BotProfile,
        operator: Identity,
        tx_type: TxType,
        target_query: str,
        amount: int,
        note: str,
        tx_id: str,
    ) -> PendingOperation:
        """Validate an award/deduct and park it until the operator confirms."""
        with self._lock:
            self._purge_expired()
            operator_user = self.authorize(operator)
            if not permissions.bot_supports(bot, tx_type):
                raise PermissionDenied(
                    f"The {bot.title} can only {self._allowed_verbs(bot)} points."
                )
            if not permissions.can_operate(operator_user, bot, tx_type):
                raise PermissionDenied(f"Your role ({operator_user.role.value}) cannot {tx_type.value} points.")

            existing = self._pending.get(tx_id)
            if existing is not None:
                # The platform re-delivered the same event: show the same confirmation.
                return existing

            transactions = self.storage.list_transactions()
            if any(t.tx_id == tx_id for t in transactions):
                raise AlreadyProcessed(tx_id)

            self._validate_amount(amount)
            target = self.find_person(target_query)
            balance = compute_balances(transactions).get(target.key, 0)

            pending = PendingOperation(
                tx_id=tx_id,
                bot=bot,
                operator=operator,
                operator_name=operator_user.name,
                target=target,
                type=tx_type,
                amount=amount,
                note=clean_note(note),
                balance_before=balance,
                created_at=self._clock(),
                requires_override=False,
            )
            if pending.balance_after < 0 and not target.allow_negative:
                if not permissions.can_override_negative(operator_user):
                    raise InsufficientBalance(target.name, balance, amount)
                pending = replace(pending, requires_override=True)

            self._pending[tx_id] = pending
            return pending

    def confirm(self, tx_id: str, operator: Identity, *, allow_negative: bool = False) -> CommitResult:
        """Write a pending operation. Safe to call any number of times."""
        with self._lock:
            pending = self._pending.get(tx_id)
            if pending is None or self._is_expired(pending):
                self._pending.pop(tx_id, None)
                existing = self._find_transaction(tx_id)
                if existing is not None:
                    return CommitResult(CommitStatus.DUPLICATE, existing, None)
                raise PendingNotFound()
            if pending.operator != operator:
                raise NotYourOperation()

            # Re-validate with fresh data: roles, people and balances may have
            # changed while the confirmation was waiting.
            operator_user = self.authorize(operator)
            if not permissions.can_operate(operator_user, pending.bot, pending.type):
                self._pending.pop(tx_id, None)
                raise PermissionDenied("You are no longer allowed to perform this operation.")

            transactions = self.storage.list_transactions()
            existing = next((t for t in transactions if t.tx_id == tx_id), None)
            if existing is not None:
                self._pending.pop(tx_id, None)
                return CommitResult(CommitStatus.DUPLICATE, existing, None)

            target = next(
                (u for u in self.storage.list_users() if u.active and u.key == pending.target.key), None
            )
            if target is None:
                self._pending.pop(tx_id, None)
                raise UnknownPerson(pending.target.name)

            balance = compute_balances(transactions).get(target.key, 0)
            balance_after = balance + pending.signed_amount
            if balance_after < 0 and not target.allow_negative:
                if not (allow_negative and permissions.can_override_negative(operator_user)):
                    self._pending.pop(tx_id, None)
                    raise InsufficientBalance(target.name, balance, pending.amount)

            tx = Transaction(
                tx_id=tx_id,
                timestamp=self._clock(),
                bot=pending.bot.key,
                operator_id=str(operator),
                operator_name=operator_user.name,
                target=target.name,
                type=pending.type,
                amount=pending.amount,
                note=pending.note,
            )
            # If this raises (e.g. Google is down) the pending operation stays,
            # so the operator can simply press "Yes" again.
            self.storage.append_transaction(tx)
            self._pending.pop(tx_id, None)
            log.info(
                "transaction %s: %s %s %d -> %s by %s",
                tx.tx_id, tx.bot, tx.type.value, tx.amount, tx.target, tx.operator_name,
            )
            return CommitResult(CommitStatus.CREATED, tx, balance_after)

    def cancel(self, tx_id: str, operator: Identity) -> bool:
        """Drop a pending operation. Returns False if there was nothing to cancel."""
        with self._lock:
            pending = self._pending.get(tx_id)
            if pending is None:
                return False
            if pending.operator != operator:
                raise NotYourOperation()
            del self._pending[tx_id]
            return True

    def get_pending(self, tx_id: str) -> PendingOperation | None:
        with self._lock:
            return self._pending.get(tx_id)

    # ---- read-only queries ----------------------------------------------------

    def balance_report(self, viewer_identity: Identity, person_query: str | None = None) -> BalanceReport:
        """Own balance for everyone; all balances (or anyone's) for admins."""
        viewer = self.authorize(viewer_identity)
        balances = compute_balances(self.storage.list_transactions())
        if person_query:
            person = self.find_person(person_query)
            if person.key != viewer.key and not permissions.can_view_all(viewer):
                raise PermissionDenied("Only administrators can see other people's balances.")
            return BalanceReport([(person.name, balances.get(person.key, 0))], everyone=False)
        if permissions.can_view_all(viewer):
            people = sorted((u for u in self.storage.list_users() if u.active), key=lambda u: u.key)
            return BalanceReport([(u.name, balances.get(u.key, 0)) for u in people], everyone=True)
        return BalanceReport([(viewer.name, balances.get(viewer.key, 0))], everyone=False)

    def history(self, viewer_identity: Identity, person_query: str | None = None, limit: int = 10) -> list[Transaction]:
        """Newest-first transactions the viewer is allowed to see.

        Non-admins see transactions where they are the target or the operator.
        Admins see everything, or one person's transactions.
        """
        viewer = self.authorize(viewer_identity)
        transactions = self.storage.list_transactions()
        if person_query:
            person = self.find_person(person_query)
            if person.key != viewer.key and not permissions.can_view_all(viewer):
                raise PermissionDenied("Only administrators can see other people's history.")
            visible = [t for t in transactions if normalize_name(t.target) == person.key]
        elif permissions.can_view_all(viewer):
            visible = transactions
        else:
            visible = [
                t for t in transactions
                if normalize_name(t.target) == viewer.key or normalize_name(t.operator_name) == viewer.key
            ]
        # Rows are appended chronologically, so the sheet order is the time order.
        return visible[::-1][:limit]

    # ---- internals ------------------------------------------------------------

    def _validate_amount(self, amount: int) -> None:
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise InvalidAmount("The amount must be a whole number.")
        if amount <= 0:
            raise InvalidAmount("The amount must be greater than zero.")
        if amount > self.max_amount:
            raise InvalidAmount(f"The amount cannot be larger than {self.max_amount}.")

    def _find_transaction(self, tx_id: str) -> Transaction | None:
        if not self.storage.transaction_exists(tx_id):
            return None
        return next((t for t in self.storage.list_transactions() if t.tx_id == tx_id), None)

    def _is_expired(self, pending: PendingOperation) -> bool:
        return self._clock() - pending.created_at > self.pending_ttl

    def _purge_expired(self) -> None:
        for tx_id in [k for k, p in self._pending.items() if self._is_expired(p)]:
            del self._pending[tx_id]

    @staticmethod
    def _allowed_verbs(bot: BotProfile) -> str:
        return " and ".join(sorted(t.value for t in bot.allowed_types))

