"""Command processing: IncomingCommand / IncomingAction -> Response.

This is the single entry point adapters call. It parses arguments, calls the
`PointsService` and builds platform-neutral rich-text replies. It never
imports anything platform-specific.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timezone
from zoneinfo import ZoneInfo

from . import permissions
from .messages import (
    Button,
    IncomingAction,
    IncomingCommand,
    Response,
    RichText,
    bold,
    code,
    italic,
    join_lines,
    pre,
    rich,
)
from .models import BotProfile, Identity, Transaction, TxType, User
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

# Button actions.
CONFIRM = "yes"
OVERRIDE = "override"
CANCEL = "no"

_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")

GENERIC_ERROR = "⚠️ Something went wrong on our side. Nothing was changed. Please try again in a minute."
BUTTON_ERROR = "⚠️ Could not reach the sheet. Please press the button again in a moment."

PLATFORM_NAMES = {"telegram": "Telegram", "discord": "Discord", "slack": "Slack", "whatsapp": "WhatsApp"}


def platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform.capitalize())


@dataclass(frozen=True)
class OperationArgs:
    person: str
    amount: int
    note: str


class UsageError(PointsError):
    pass


def parse_operation_args(args: str, tx_type: TxType) -> OperationArgs:
    """Parse "<person name> <amount> [note]". Names may contain spaces."""
    usage = f"Usage: /{tx_type.value} <name> <amount> [note]\nExample: /{tx_type.value} Anna 10 great work"
    tokens = args.split()
    index = next((i for i, tok in enumerate(tokens) if i > 0 and _NUMBER.match(tok)), None)
    if index is None:
        raise UsageError(usage)
    raw = tokens[index]
    if not re.fullmatch(r"\+?\d+", raw):
        raise UsageError("The amount must be a positive whole number, e.g. 10.")
    return OperationArgs(person=" ".join(tokens[:index]), amount=int(raw), note=" ".join(tokens[index + 1 :]))


def points(n: int) -> str:
    return f"{n} point" if abs(n) == 1 else f"{n} points"


class CommandProcessor:
    """Handles commands and button presses for one bot profile."""

    def __init__(self, service: PointsService, bot: BotProfile, tz: ZoneInfo | timezone = timezone.utc):
        self.service = service
        self.bot = bot
        self.tz = tz

    # ---- entry points ---------------------------------------------------------

    def handle_command(self, cmd: IncomingCommand) -> Response:
        sender = cmd.sender
        try:
            if cmd.command == "myid":
                return Response(rich(f"Your {platform_name(sender.platform)} ID is ", code(sender.user_id), "."))
            if cmd.command in ("start", "help"):
                return self._help(sender)
            if cmd.command in (TxType.AWARD.value, TxType.DEDUCT.value):
                tx_id = make_tx_id(self.bot.key, cmd.event_id)
                return self._operation(sender, TxType(cmd.command), cmd.args, tx_id)
            if cmd.command == "balance":
                return self._balance(sender, cmd.args or None)
            if cmd.command == "history":
                return self._history(sender, cmd.args or None)
            self.service.authorize(sender)
            return Response(rich("Unknown command. Send /help to see what I can do."))
        except PointsError as exc:
            return self._error_reply(exc)
        except Exception:
            log.exception("unexpected error while handling /%s from %s", cmd.command, sender)
            return Response(rich(GENERIC_ERROR))

    def handle_action(self, act: IncomingAction) -> Response:
        """Handle a Yes / No / "Yes, allow negative" press."""
        tx_id = act.token
        try:
            if act.action == CANCEL:
                cancelled = self.service.cancel(tx_id, act.sender)
                if not cancelled and self.service.storage.transaction_exists(tx_id):
                    return Response(rich("ℹ️ This operation was already recorded, it cannot be cancelled here."))
                return Response(rich("❌ Cancelled. Nothing was recorded."))
            if act.action not in (CONFIRM, OVERRIDE):
                return Response(rich("Unknown action."), alert_only=True)
            result = self.service.confirm(tx_id, act.sender, allow_negative=(act.action == OVERRIDE))
        except NotYourOperation as exc:
            return Response(rich(str(exc)), alert_only=True)
        except PointsError as exc:
            return self._error_reply(exc)
        except Exception:
            log.exception("unexpected error while confirming %s", tx_id)
            return Response(rich(BUTTON_ERROR), alert_only=True)

        tx = result.transaction
        if result.status is CommitStatus.DUPLICATE:
            return Response(
                rich(
                    "ℹ️ This operation was already recorded. Nothing was changed.\nTransaction ID: ", code(tx.tx_id)
                )
            )
        verb, prep = ("Awarded", "to") if tx.type is TxType.AWARD else ("Deducted", "from")
        return Response(
            rich(
                f"✅ Done. {verb} ", bold(points(tx.amount)), f" {prep} ", bold(tx.target), ".\n",
                "New balance: ", bold(result.balance_after), "\n",
                "Transaction ID: ", code(tx.tx_id),
            )
        )  # fmt: skip

    # ---- commands -------------------------------------------------------------

    def _help(self, sender: Identity) -> Response:
        user = self.service.authorize(sender)
        lines: list[RichText] = [rich("👋 Hi, ", bold(user.name), "! This is the ", bold(self.bot.title), "."), ()]
        for tx_type in (TxType.AWARD, TxType.DEDUCT):
            if permissions.can_operate(user, self.bot, tx_type):
                lines.append(rich(f"/{tx_type.value} <name> <amount> [note] — {tx_type.value} points"))
        if permissions.can_view_all(user):
            lines.append(rich("/balance — everyone's balance (or /balance <name>)"))
            lines.append(rich("/history — last 10 transactions (or /history <name>)"))
        else:
            lines.append(rich("/balance — your balance"))
            lines.append(rich("/history — your last 10 transactions"))
        lines.append(rich(f"/myid — your {platform_name(sender.platform)} ID"))
        lines.append(rich("/help — this message"))
        if self.bot.allowed_types == {TxType.DEDUCT}:
            lines += [(), rich("ℹ️ This bot can only deduct points.")]
        lines += [(), rich("Every award or deduction asks for a confirmation before it is saved.")]
        return Response(join_lines(lines))

    def _operation(self, sender: Identity, tx_type: TxType, args: str, tx_id: str) -> Response:
        # Check access before parsing, so strangers never learn the syntax.
        self.service.authorize(sender)
        parsed = parse_operation_args(args, tx_type)
        pending = self.service.prepare(
            bot=self.bot,
            operator=sender,
            tx_type=tx_type,
            target_query=parsed.person,
            amount=parsed.amount,
            note=parsed.note,
            tx_id=tx_id,
        )
        return self._confirmation(pending)

    def _confirmation(self, p: PendingOperation) -> Response:
        verb, prep = ("Award", "to") if p.type is TxType.AWARD else ("Deduct", "from")
        lines = [
            rich(f"{verb} ", bold(points(p.amount)), f" {prep} ", bold(p.target.name), "?"),
            rich(f"Balance: {p.balance_before} → ", bold(p.balance_after)),
        ]
        if p.note:
            lines.append(rich("Note: ", p.note))
        if p.requires_override:
            warning = rich("⚠️ The balance would go ", bold("below zero"), ". As an administrator you can allow it.")
            lines += [(), warning]
            yes = Button("⚠️ Yes, allow negative", OVERRIDE, p.tx_id)
        else:
            yes = Button("✅ Yes", CONFIRM, p.tx_id)
        return Response(join_lines(lines), buttons=(yes, Button("❌ No", CANCEL, p.tx_id)))

    def _balance(self, sender: Identity, person: str | None) -> Response:
        report = self.service.balance_report(sender, person)
        if report.everyone:
            if not report.entries:
                return Response(rich("No people in the Users sheet yet."))
            width = max(len(name) for name, _ in report.entries)
            rows = "\n".join(f"{name.ljust(width)}  {value:>7}" for name, value in report.entries)
            total = sum(v for _, v in report.entries)
            return Response(rich("📊 ", bold("All balances"), "\n", pre(rows), "\nTotal: ", bold(total)))
        name, value = report.entries[0]
        viewer = self.service.find_user(sender)
        if viewer is not None and viewer.name == name:
            return Response(rich("💰 Your balance: ", bold(points(value))))
        return Response(rich(f"💰 {name}: ", bold(points(value))))

    def _history(self, sender: Identity, person: str | None) -> Response:
        items = self.service.history(sender, person)
        if not items:
            return Response(rich("No transactions yet."))
        title = rich("🧾 ", bold(f"Last {len(items)} transactions"), f" for {person}" if person else "")
        return Response(join_lines([title] + [self._format_tx(tx) for tx in items]))

    def _format_tx(self, tx: Transaction) -> RichText:
        when = tx.timestamp.astimezone(self.tz).strftime("%Y-%m-%d %H:%M")
        line = rich(f"{when}  ", bold(f"{tx.signed_amount:+d}"), f"  {tx.target} · by {tx.operator_name}")
        return rich(line, " · ", italic(tx.note)) if tx.note else line

    # ---- errors ---------------------------------------------------------------

    def _error_reply(self, exc: PointsError) -> Response:
        if isinstance(exc, AccessDenied):
            return Response(
                rich(
                    "🙏 Sorry, you don't have access to this bot.\n",
                    f"Your {platform_name(exc.identity.platform)} ID is ", code(exc.identity.user_id), ". ",
                    "If you think you should have access, send this ID to the administrator.",
                )
            )  # fmt: skip
        if isinstance(exc, UsageError):
            return Response(rich(str(exc)))
        if isinstance(exc, AlreadyProcessed):
            return Response(rich(f"ℹ️ {exc}"))
        if isinstance(exc, AmbiguousPerson):
            return Response(rich(f"⛔ {exc}\nPlease type the full name."))
        if isinstance(exc, UnknownPerson) and exc.suggestions:
            return Response(rich(f"⛔ {exc}\nDid you mean: {', '.join(exc.suggestions[:5])}?"))
        return Response(rich(f"⛔ {exc}"))


def describe_user(user: User) -> str:
    ids = ", ".join(f"{platform_name(p)} {i}" for p, i in user.ids.items() if i) or "no messaging account"
    return f"{user.name} ({user.role.value}, {ids})"
