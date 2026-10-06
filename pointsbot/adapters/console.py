"""Console adapter: talk to the core from a terminal, without any messaging platform.

    python -m pointsbot console            # in-memory demo data (MOCK)
    python -m pointsbot console --sheets   # the real Google Sheet from .env
    python -m pointsbot demo               # scripted walkthrough of every feature

It is a complete adapter in ~150 lines and the reference for writing new ones:
it builds `IncomingCommand` / `IncomingAction`, calls the core, and renders the
`Response` as plain text. To act as someone it impersonates their platform
identity (by default their Telegram ID from the Users sheet).
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from zoneinfo import ZoneInfo

from ..core import (
    BOT_PROFILES,
    CANCEL,
    CONFIRM,
    OVERRIDE,
    CommandProcessor,
    Identity,
    IncomingAction,
    IncomingCommand,
    PointsService,
    Response,
    parse_command_text,
)
from ..core.commands import describe_user
from ..core.service import compute_balances
from ..core.storage.memory import DEMO_ADMIN, DEMO_MEMBER, DEMO_OPERATOR, DEMO_PLATFORM, DEMO_STRANGER, demo_storage

CONSOLE_HELP = """\
Type bot commands exactly as in a chat, e.g. /deduct Mia 5 coffee
Console commands:
  :bot deduct|points   switch bot              :as <id> | <platform>:<id>  act as another user
  :users               list users              :sheet             show Transactions + Balances
  :yes  :no  :override press a button          :tap               press the last pressed button again
  :retry               re-deliver the last command (simulates a platform retry)
  :help                this help               :quit              exit
"""


def render(response: Response) -> str:
    text = response.plain
    if response.alert_only:
        text = f"[pop-up] {text}"
    if response.buttons:
        text += "\n" + "  ".join(f"[{b.text}]" for b in response.buttons)
    return text


class ConsoleSession:
    """Simulates one chat per user with both bots."""

    def __init__(self, service: PointsService, tz: ZoneInfo | None = None):
        self.service = service
        tz = tz or ZoneInfo("Europe/Vilnius")
        self.processors = {key: CommandProcessor(service, profile, tz) for key, profile in BOT_PROFILES.items()}
        self.bot = "points"
        self.sender = DEMO_ADMIN
        self._event_counter = 100
        self._last_command: tuple[str, str] | None = None  # (text, event_id)
        self._last_buttons: dict[str, str] = {}  # action -> token
        self._last_press: tuple[str, str] | None = None

    @property
    def prompt(self) -> str:
        user = self.service.find_user(self.sender)
        who = user.name if user else "unknown user"
        return f"[{self.bot} bot | {who} {self.sender}]> "

    def send(self, text: str, *, event_id: str | None = None) -> str:
        parsed = parse_command_text(text)
        if parsed is None:
            return "Commands start with /. Type :help for console commands."
        if event_id is None:
            self._event_counter += 1
            event_id = f"console-{self._event_counter}"
        self._last_command = (text, event_id)
        command, _mention, args = parsed
        response = self.processors[self.bot].handle_command(IncomingCommand(self.sender, event_id, command, args))
        if response.buttons:
            self._last_buttons = {b.action: b.token for b in response.buttons}
        return render(response)

    def retry(self) -> str:
        if self._last_command is None:
            return "Nothing to retry."
        text, event_id = self._last_command
        return self.send(text, event_id=event_id)

    def press(self, action: str) -> str:
        token = self._last_buttons.get(action)
        if token is None:
            return "There is no such button on the last message."
        self._last_press = (action, token)
        return render(self.processors[self.bot].handle_action(IncomingAction(self.sender, action, token)))

    def tap_again(self) -> str:
        if self._last_press is None:
            return "No button was pressed yet."
        action, token = self._last_press
        return render(self.processors[self.bot].handle_action(IncomingAction(self.sender, action, token)))

    def users_table(self) -> str:
        return "\n".join(describe_user(u) for u in self.service.storage.list_users())

    def sheet_tables(self) -> str:
        txs = self.service.storage.list_transactions()
        lines = ["Transactions:"]
        for t in txs:
            ts = t.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(
                f"  {t.tx_id:<24} {ts}  {t.bot:<6} {t.operator_name:<6} {t.target:<6} "
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
        if command == "as" and len(parts) == 2:
            platform, _, user_id = parts[1].rpartition(":")
            self.sender = Identity(platform or DEMO_PLATFORM, user_id)
            return f"Now acting as {self.sender}."
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


def make_session(service: PointsService | None = None, tz: ZoneInfo | None = None) -> ConsoleSession:
    return ConsoleSession(service or PointsService(demo_storage()), tz)


def run_console(service: PointsService | None = None, tz: ZoneInfo | None = None) -> None:
    session = make_session(service, tz)
    source = "in-memory demo sheet" if service is None else "the Google Sheet from .env"
    print(f"Points bots — console adapter ({source})\n")
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


DEMO_STEPS: list[tuple[str, str | None, Identity | None, str]] = [
    # (narration, bot, sender, input)
    ("A stranger tries the bot and gets a polite refusal", "points", DEMO_STRANGER, "/balance"),
    ("Member Mia checks her own balance", "points", DEMO_MEMBER, "/balance"),
    ("Mia (member) is not allowed to deduct", None, None, "/deduct Leo 5"),
    ("The Deduct bot refuses to award, even for an admin", "deduct", DEMO_ADMIN, "/award Mia 5"),
    ("Operator Oscar deducts 10 from Mia — a confirmation appears", "deduct", DEMO_OPERATOR,
     "/deduct Mia 10 late submission"),
    ("Oscar presses Yes", None, None, ":yes"),
    ("Oscar double-taps Yes — ignored, no second row", None, None, ":tap"),
    ("The platform re-delivers the same command — ignored", None, None, ":retry"),
    ("Oscar tries to deduct more than Leo has", None, None, "/deduct Leo 50"),
    ("Admin Anna awards points with the Points bot", "points", DEMO_ADMIN, "/award Leo 15 helped a colleague"),
    ("Anna confirms", None, None, ":yes"),
    ("Anna deducts more than Mia has: she must explicitly allow a negative balance", None, None,
     "/deduct Mia 100 equipment damage"),
    ("Anna changes her mind and presses No", None, None, ":no"),
    ("Admin sees everyone's balance", None, None, "/balance"),
    ("Admin sees the last transactions", None, None, "/history"),
    ("The sheet: append-only Transactions + formula-based Balances", None, None, ":sheet"),
]  # fmt: skip


def run_demo(steps: Iterable[tuple[str, str | None, Identity | None, str]] = DEMO_STEPS) -> ConsoleSession:
    session = make_session()
    for number, (narration, bot, sender, line) in enumerate(steps, start=1):
        if bot:
            session.bot = bot
        if sender:
            session.sender = sender
        print(f"── {number}. {narration}")
        print(session.prompt + line)
        print(session.execute(line))
        print()
    return session
