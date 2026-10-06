"""Command parsing and reply formatting, independent of any transport.

Both the Telegram handlers and the mock console call `CommandHandler`, so the
console exercises exactly the same code path as the real bots.
Replies are Telegram-flavoured HTML; the console strips the tags.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timezone
from html import escape, unescape
from zoneinfo import ZoneInfo

from . import permissions
from .models import BotProfile, Transaction, TxType, User
from .service import (
    AccessDenied,
    AlreadyProcessed,
    AmbiguousPerson,
    CommitStatus,
    NotYourOperation,
    PendingOperation,
    PointsError,
    PointsService,
    UnknownPerson,
    make_tx_id,
)

log = logging.getLogger(__name__)

# Button actions (also used in Telegram callback data).
CONFIRM = "yes"
OVERRIDE = "override"
CANCEL = "no"

_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")

GENERIC_ERROR = "⚠️ Something went wrong on our side. Nothing was changed. Please try again in a minute."
BUTTON_ERROR = "⚠️ Could not reach the sheet. Please press the button again in a moment."


@dataclass(frozen=True)
class Button:
    text: str
    action: str
    tx_id: str


@dataclass(frozen=True)
class Reply:
    text: str
    buttons: tuple[Button, ...] = ()
    # For button presses: show a short pop-up and keep the original message as is.
    alert_only: bool = False


@dataclass(frozen=True)
class OperationArgs:
    person: str
    amount: int
    note: str


class UsageError(PointsError):
    pass


def parse_operation_args(args: str, tx_type: TxType) -> OperationArgs:
    """Parse "<person name> <amount> [note]". Names may contain spaces."""
    usage = f"Usage: /{tx_type.value} &lt;name&gt; &lt;amount&gt; [note]\nExample: /{tx_type.value} Anna 10 great work"
    tokens = args.split()
    index = next((i for i, tok in enumerate(tokens) if i > 0 and _NUMBER.match(tok)), None)
    if index is None:
        raise UsageError(usage)
    raw = tokens[index]
    if not re.fullmatch(r"\+?\d+", raw):
        raise UsageError("The amount must be a positive whole number, e.g. 10.")
    return OperationArgs(person=" ".join(tokens[:index]), amount=int(raw), note=" ".join(tokens[index + 1:]))


def split_command(text: str) -> tuple[str, str | None, str]:
    """"/award@MyBot Anna 5" -> ("award", "MyBot", "Anna 5")."""
    head, _, rest = text.strip().partition(" ")
    command, _, mention = head.lstrip("/").partition("@")
    return command.lower(), (mention or None), rest.strip()


def points(n: int) -> str:
    return f"{n} point" if abs(n) == 1 else f"{n} points"


class CommandHandler:
    """Handles commands for one bot profile."""

    def __init__(self, service: PointsService, bot: BotProfile, tz: ZoneInfo | timezone = timezone.utc):
        self.service = service
        self.bot = bot
        self.tz = tz

    # ---- entry points ---------------------------------------------------------

    def handle_command(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_id: int,
        text: str,
        bot_username: str | None = None,
    ) -> Reply | None:
        """Return the reply for a "/command" message, or None to stay silent."""
        command, mention, args = split_command(text)
        if mention and bot_username and mention.lower() != bot_username.lower():
            return None  # the command is addressed to another bot in a group chat
        try:
            if command == "myid":
                return Reply(f"Your Telegram ID is <code>{user_id}</code>.")
            if command in ("start", "help"):
                return self._help(user_id)
            if command in (TxType.AWARD.value, TxType.DEDUCT.value):
                tx_id = make_tx_id(self.bot.key, chat_id, message_id)
                return self._operation(user_id, TxType(command), args, tx_id)
            if command == "balance":
                return self._balance(user_id, args or None)
            if command == "history":
                return self._history(user_id, args or None)
            self.service.authorize(user_id)
            return Reply("Unknown command. Send /help to see what I can do.")
        except PointsError as exc:
            return self._error_reply(exc)
        except Exception:
            log.exception("unexpected error while handling %r", text)
            return Reply(GENERIC_ERROR)

    def handle_button(self, *, user_id: int, action: str, tx_id: str) -> Reply:
        """Handle a Yes / No / "Yes, allow negative" press."""
        try:
            if action == CANCEL:
                cancelled = self.service.cancel(tx_id, user_id)
                if not cancelled and self.service.storage.transaction_exists(tx_id):
                    return Reply("ℹ️ This operation was already recorded, it cannot be cancelled here.")
                return Reply("❌ Cancelled. Nothing was recorded.")
            if action not in (CONFIRM, OVERRIDE):
                return Reply("Unknown action.", alert_only=True)
            result = self.service.confirm(tx_id, user_id, allow_negative=(action == OVERRIDE))
        except PointsError as exc:
            reply = self._error_reply(exc)
            return Reply(_strip_tags(reply.text), alert_only=True) if _is_soft(exc) else reply
        except Exception:
            log.exception("unexpected error while confirming %s", tx_id)
            return Reply(BUTTON_ERROR, alert_only=True)

        tx = result.transaction
        if result.status is CommitStatus.DUPLICATE:
            return Reply(
                "ℹ️ This operation was already recorded. Nothing was changed.\n"
                f"Transaction ID: <code>{escape(tx.tx_id)}</code>"
            )
        verb = "Awarded" if tx.type is TxType.AWARD else "Deducted"
        prep = "to" if tx.type is TxType.AWARD else "from"
        return Reply(
            f"✅ Done. {verb} <b>{points(tx.amount)}</b> {prep} <b>{escape(tx.target)}</b>.\n"
            f"New balance: <b>{result.balance_after}</b>\n"
            f"Transaction ID: <code>{escape(tx.tx_id)}</code>"
        )

    # ---- commands -------------------------------------------------------------

    def _help(self, user_id: int) -> Reply:
        user = self.service.authorize(user_id)
        lines = [f"👋 Hi, <b>{escape(user.name)}</b>! This is the <b>{self.bot.title}</b>.", ""]
        for tx_type in (TxType.AWARD, TxType.DEDUCT):
            if permissions.can_operate(user, self.bot, tx_type):
                lines.append(f"/{tx_type.value} &lt;name&gt; &lt;amount&gt; [note] — {tx_type.value} points")
        if permissions.can_view_all(user):
            lines.append("/balance — everyone's balance (or /balance &lt;name&gt;)")
            lines.append("/history — last 10 transactions (or /history &lt;name&gt;)")
        else:
            lines.append("/balance — your balance")
            lines.append("/history — your last 10 transactions")
        lines.append("/myid — your Telegram ID")
        lines.append("/help — this message")
        if self.bot.allowed_types == {TxType.DEDUCT}:
            lines += ["", "ℹ️ This bot can only deduct points."]
        lines += ["", "Every award or deduction asks for a confirmation before it is saved."]
        return Reply("\n".join(lines))

    def _operation(self, user_id: int, tx_type: TxType, args: str, tx_id: str) -> Reply:
        # Check access before parsing, so strangers never learn the syntax.
        self.service.authorize(user_id)
        parsed = parse_operation_args(args, tx_type)
        pending = self.service.prepare(
            bot=self.bot,
            operator_id=user_id,
            tx_type=tx_type,
            target_query=parsed.person,
            amount=parsed.amount,
            note=parsed.note,
            tx_id=tx_id,
        )
        return self._confirmation(pending)

    def _confirmation(self, p: PendingOperation) -> Reply:
        verb = "Award" if p.type is TxType.AWARD else "Deduct"
        prep = "to" if p.type is TxType.AWARD else "from"
        lines = [
            f"{verb} <b>{points(p.amount)}</b> {prep} <b>{escape(p.target.name)}</b>?",
            f"Balance: {p.balance_before} → <b>{p.balance_after}</b>",
        ]
        if p.note:
            lines.append(f"Note: {escape(p.note)}")
        if p.requires_override:
            lines += ["", "⚠️ The balance would go <b>below zero</b>. As an administrator you can allow it."]
            yes = Button("⚠️ Yes, allow negative", OVERRIDE, p.tx_id)
        else:
            yes = Button("✅ Yes", CONFIRM, p.tx_id)
        return Reply("\n".join(lines), buttons=(yes, Button("❌ No", CANCEL, p.tx_id)))

    def _balance(self, user_id: int, person: str | None) -> Reply:
        report = self.service.balance_report(user_id, person)
        if report.everyone:
            if not report.entries:
                return Reply("No people in the Users sheet yet.")
            width = max(len(name) for name, _ in report.entries)
            rows = "\n".join(f"{escape(name.ljust(width))}  {value:>7}" for name, value in report.entries)
            total = sum(v for _, v in report.entries)
            return Reply(f"📊 <b>All balances</b>\n<pre>{rows}</pre>\nTotal: <b>{total}</b>")
        name, value = report.entries[0]
        viewer = self.service.find_user(user_id)
        if viewer is not None and viewer.name == name:
            return Reply(f"💰 Your balance: <b>{points(value)}</b>")
        return Reply(f"💰 {escape(name)}: <b>{points(value)}</b>")

    def _history(self, user_id: int, person: str | None) -> Reply:
        items = self.service.history(user_id, person)
        if not items:
            return Reply("No transactions yet.")
        title = f"for {escape(person)}" if person else ""
        lines = [f"🧾 <b>Last {len(items)} transactions</b> {title}".rstrip()]
        lines += [self._format_tx(tx) for tx in items]
        return Reply("\n".join(lines))

    def _format_tx(self, tx: Transaction) -> str:
        when = tx.timestamp.astimezone(self.tz).strftime("%Y-%m-%d %H:%M")
        line = f"{when}  <b>{tx.signed_amount:+d}</b>  {escape(tx.target)} · by {escape(tx.operator_name)}"
        if tx.note:
            line += f" · <i>{escape(tx.note)}</i>"
        return line

    # ---- errors ---------------------------------------------------------------

    def _error_reply(self, exc: PointsError) -> Reply:
        if isinstance(exc, AccessDenied):
            return Reply(
                "🙏 Sorry, you don't have access to this bot.\n"
                f"Your Telegram ID is <code>{exc.telegram_id}</code>. "
                "If you think you should have access, send this ID to the administrator."
            )
        if isinstance(exc, UsageError):
            return Reply(str(exc))  # already HTML-safe
        text = f"⛔ {escape(str(exc))}"
        if isinstance(exc, UnknownPerson) and exc.suggestions:
            text += "\nDid you mean: " + ", ".join(escape(s) for s in exc.suggestions[:5]) + "?"
        if isinstance(exc, AmbiguousPerson):
            text = f"⛔ {escape(str(exc))}\nPlease type the full name."
        if isinstance(exc, AlreadyProcessed):
            text = f"ℹ️ {escape(str(exc))}"
        return Reply(text)


def _is_soft(exc: PointsError) -> bool:
    """Errors on button presses that should not replace the confirmation message."""
    return isinstance(exc, NotYourOperation)


def _strip_tags(text: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", text))


def describe_user(user: User) -> str:
    tid = user.telegram_id if user.telegram_id is not None else "no Telegram"
    return f"{user.name} ({user.role.value}, {tid})"
