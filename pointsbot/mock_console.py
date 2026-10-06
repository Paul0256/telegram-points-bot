"""MOCK mode: drive both bots from the terminal, without Telegram or Google.

    python -m pointsbot console   # interactive
    python -m pointsbot demo      # scripted walkthrough of every feature

Messages go through the same `CommandHandler` as the real Telegram bots.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from html import unescape
from zoneinfo import ZoneInfo

from .commands import CANCEL, CONFIRM, OVERRIDE, CommandHandler, Reply, describe_user
from .models import BOT_PROFILES
from .service import PointsService, compute_balances
from .storage.memory import (
    DEMO_ADMIN_ID,
    DEMO_MEMBER_ID,
    DEMO_OPERATOR_ID,
    DEMO_STRANGER_ID,
    InMemoryStorage,
    demo_storage,
)

CONSOLE_HELP = """\
Type bot commands exactly as in Telegram, e.g. /deduct Mia 5 coffee
Console commands:
  :bot deduct|points   switch bot              :as <telegram_id>  act as another user
  :users               list users              :sheet             show Transactions + Balances
  :yes  :no  :override press a button          :tap               press the last pressed button again
  :retry               re-deliver the last command (simulates a Telegram retry)
  :help                this help               :quit              exit
"""


def to_plain(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html))


def render(reply: Reply | None) -> str:
    if reply is None:
        return "(no reply)"
    text = to_plain(reply.text)
    if reply.alert_only:
        text = f"[pop-up] {text}"
    if reply.buttons:
        text += "\n" + "  ".join(f"[{b.text}]" for b in reply.buttons)
    return text


class MockSession:
    """Simulates one Telegram chat per user, for both bots."""

    def __init__(self, service: PointsService, tz: ZoneInfo | None = None):
        self.service = service
        tz = tz or ZoneInfo("Europe/Vilnius")
        self.handlers = {key: CommandHandler(service, profile, tz) for key, profile in BOT_PROFILES.items()}
        self.bot = "points"
        self.user_id = DEMO_ADMIN_ID
        self._next_message_id = 100
        self._last_command: tuple[str, int] | None = None
        self._last_buttons: dict[str, str] = {}  # action -> tx_id
        self._last_press: tuple[str, str] | None = None

    @property
    def prompt(self) -> str:
        user = self.service.find_user(self.user_id)
        who = user.name if user else "unknown user"
        return f"[{self.bot} bot | {who} #{self.user_id}]> "

    def send(self, text: str, *, message_id: int | None = None) -> str:
        if message_id is None:
            self._next_message_id += 1
            message_id = self._next_message_id
        self._last_command = (text, message_id)
        reply = self.handlers[self.bot].handle_command(
            user_id=self.user_id, chat_id=self.user_id, message_id=message_id, text=text
        )
        if reply is not None and reply.buttons:
            self._last_buttons = {b.action: b.tx_id for b in reply.buttons}
        return render(reply)

    def retry(self) -> str:
        if self._last_command is None:
            return "Nothing to retry."
        text, message_id = self._last_command
        return self.send(text, message_id=message_id)

    def press(self, action: str) -> str:
        tx_id = self._last_buttons.get(action)
        if tx_id is None:
            return "There is no such button on the last message."
        self._last_press = (action, tx_id)
        return render(self.handlers[self.bot].handle_button(user_id=self.user_id, action=action, tx_id=tx_id))

    def tap_again(self) -> str:
        if self._last_press is None:
            return "No button was pressed yet."
        action, tx_id = self._last_press
        return render(self.handlers[self.bot].handle_button(user_id=self.user_id, action=action, tx_id=tx_id))

    def users_table(self) -> str:
        return "\n".join(describe_user(u) for u in self.service.storage.list_users())

    def sheet_tables(self) -> str:
        txs = self.service.storage.list_transactions()
        lines = ["Transactions:"]
        for t in txs:
            ts = t.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(
                f"  {t.tx_id:<22} {ts}  {t.bot:<6} {t.operator_name:<6} {t.target:<6} "
                f"{t.type.value:<6} {t.amount:>5}  {t.note}"
            )
        balances = compute_balances(txs)
        lines.append("Balances (formula view):")
        for user in sorted(self.service.storage.list_users(), key=lambda u: u.key):
            lines.append(f"  {user.name:<8} {balances.get(user.key, 0):>6}")
        return "\n".join(lines)

    def execute(self, line: str) -> str | None:
        """Run one console line. Returns None when the user wants to quit."""
        line = line.strip()
        if not line:
            return ""
        if line.startswith("/"):
            return self.send(line)
        parts = shlex.split(line[1:]) if line.startswith(":") else []
        command = parts[0].lower() if parts else ""
        if command in ("quit", "exit", "q"):
            return None
        if command == "bot" and len(parts) == 2 and parts[1] in BOT_PROFILES:
            self.bot = parts[1]
            return f"Now talking to the {BOT_PROFILES[self.bot].title}."
        if command == "as" and len(parts) == 2 and parts[1].lstrip("-").isdigit():
            self.user_id = int(parts[1])
            return f"Now acting as Telegram user {self.user_id}."
        if command == "users":
            return self.users_table()
        if command == "sheet":
            return self.sheet_tables()
        if command in ("yes", "no", "override"):
            return self.press({"yes": CONFIRM, "no": CANCEL, "override": OVERRIDE}[command])
        if command == "tap":
            return self.tap_again()
        if command == "retry":
            return self.retry()
        return CONSOLE_HELP


def make_session(storage: InMemoryStorage | None = None, tz: ZoneInfo | None = None) -> MockSession:
    return MockSession(PointsService(storage or demo_storage()), tz)


def run_console() -> None:
    session = make_session()
    print("Points bot — MOCK console (in-memory sheet, no Telegram, no Google)\n")
    print(CONSOLE_HELP)
    print("Users:\n" + session.users_table() + "\n")
    while True:
        try:
            line = input(session.prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        output = session.execute(line)
        if output is None:
            break
        if output:
            print(output + "\n")


DEMO_STEPS: list[tuple[str, str | None, int | None, str]] = [
    # (narration, bot, user, input)
    ("A stranger tries the bot and gets a polite refusal", "points", DEMO_STRANGER_ID, "/balance"),
    ("Member Mia checks her own balance", "points", DEMO_MEMBER_ID, "/balance"),
    ("Mia (member) is not allowed to deduct", None, None, "/deduct Leo 5"),
    ("The Deduct bot refuses to award, even for an admin", "deduct", DEMO_ADMIN_ID, "/award Mia 5"),
    ("Operator Oscar deducts 10 from Mia — a confirmation appears", "deduct", DEMO_OPERATOR_ID,
     "/deduct Mia 10 late submission"),
    ("Oscar presses Yes", None, None, ":yes"),
    ("Oscar double-taps Yes — ignored, no second row", None, None, ":tap"),
    ("Telegram re-delivers the same command — ignored", None, None, ":retry"),
    ("Oscar tries to deduct more than Leo has", None, None, "/deduct Leo 50"),
    ("Admin Anna awards points with the Points bot", "points", DEMO_ADMIN_ID, "/award Leo 15 helped a colleague"),
    ("Anna confirms", None, None, ":yes"),
    ("Anna deducts more than Mia has: she must explicitly allow a negative balance", None, None,
     "/deduct Mia 100 equipment damage"),
    ("Anna changes her mind and presses No", None, None, ":no"),
    ("Admin sees everyone's balance", None, None, "/balance"),
    ("Admin sees the last transactions", None, None, "/history"),
    ("The sheet: append-only Transactions + formula-based Balances", None, None, ":sheet"),
]


def run_demo(steps: Iterable[tuple[str, str | None, int | None, str]] = DEMO_STEPS) -> MockSession:
    session = make_session()
    for number, (narration, bot, user_id, line) in enumerate(steps, start=1):
        if bot:
            session.bot = bot
        if user_id:
            session.user_id = user_id
        print(f"── {number}. {narration}")
        print(session.prompt + line)
        print(session.execute(line))
        print()
    return session


__all__ = ["MockSession", "make_session", "run_console", "run_demo"]
