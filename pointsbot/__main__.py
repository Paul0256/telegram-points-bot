"""Command-line entry point.

    python -m pointsbot run                  # start both Telegram bots
    python -m pointsbot console              # MOCK: chat with the bots in the terminal
    python -m pointsbot demo                 # MOCK: scripted walkthrough
    python -m pointsbot setup-sheet --admin-id 123 --admin-name "Anna"
    python -m pointsbot check                # verify .env, Google access and the Users sheet
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import timedelta

from dotenv import load_dotenv

from .config import ConfigError, Settings
from .service import PointsService


def build_service(settings: Settings) -> PointsService:
    if settings.mock:
        from .storage.memory import demo_storage

        logging.getLogger(__name__).warning("MOCK=true: using an in-memory sheet with demo data")
        storage = demo_storage()
    else:
        problems = settings.sheet_problems()
        if problems:
            raise ConfigError("\n".join(problems))
        from .storage.gsheets import GoogleSheetsStorage, check_spreadsheet

        storage = GoogleSheetsStorage.connect(
            settings.service_account_file, settings.spreadsheet_id, users_cache_seconds=settings.users_cache_seconds
        )
        check_spreadsheet(storage.spreadsheet)
    return PointsService(
        storage, max_amount=settings.max_amount, pending_ttl=timedelta(seconds=settings.pending_ttl_seconds)
    )


def cmd_run(settings: Settings) -> int:
    problems = settings.telegram_problems()
    if problems:
        raise ConfigError("\n".join(problems))
    from .telegram.app import run_bots

    service = build_service(settings)
    try:
        asyncio.run(run_bots(settings, service))
    except KeyboardInterrupt:
        pass
    return 0


def cmd_setup_sheet(settings: Settings, admin_id: int | None, admin_name: str | None) -> int:
    problems = settings.sheet_problems()
    if problems:
        raise ConfigError("\n".join(problems))
    from .storage.gsheets import open_spreadsheet, setup_spreadsheet

    if (admin_id is None) != (admin_name is None):
        raise ConfigError("Use --admin-id and --admin-name together.")
    spreadsheet = open_spreadsheet(settings.service_account_file, settings.spreadsheet_id)
    admin = (admin_id, admin_name) if admin_id is not None else None
    for step in setup_spreadsheet(spreadsheet, admin):
        print(f"✔ {step}")
    print(f"\nDone: {spreadsheet.url}")
    return 0


def cmd_check(settings: Settings) -> int:
    print(f"Mode: {'MOCK (in-memory)' if settings.mock else 'Google Sheets'}")
    tg = settings.telegram_problems()
    print("Telegram tokens: " + ("OK" if not tg else "\n  - " + "\n  - ".join(tg)))
    if settings.mock:
        return 1 if tg else 0
    service = build_service(settings)
    storage = service.storage
    users = storage.list_users()
    txs = storage.list_transactions()
    print(f"Google Sheet: OK ({len(users)} people, {len(txs)} transactions)")
    issues = list(getattr(storage, "warnings", []))
    seen: dict[str, str] = {}
    for user in users:
        if user.key in seen:
            issues.append(f'Duplicate name "{user.name}" in Users: names must be unique')
        seen[user.key] = user.name
        if user.name != user.name.strip():
            issues.append(f'Name "{user.name}" has leading/trailing spaces')
    ids = [u.telegram_id for u in users if u.telegram_id is not None]
    if len(ids) != len(set(ids)):
        issues.append("The same telegram_id appears more than once in Users")
    if not any(u.is_admin and u.active and u.telegram_id for u in users):
        issues.append("No active admin with a telegram_id: nobody can see all balances")
    for issue in issues:
        print(f"  ⚠ {issue}")
    print("All good." if not issues and not tg else "Please fix the items above.")
    return 1 if issues or tg else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pointsbot", description="Telegram points bots with Google Sheets")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="start both Telegram bots")
    sub.add_parser("console", help="MOCK: talk to the bots in the terminal")
    sub.add_parser("demo", help="MOCK: run a scripted walkthrough")
    setup = sub.add_parser("setup-sheet", help="create the Users/Transactions/Balances tabs")
    setup.add_argument("--admin-id", type=int, help="your Telegram ID (adds you as the first admin)")
    setup.add_argument("--admin-name", help="your name as it should appear in the sheet")
    sub.add_parser("check", help="verify configuration and the sheet")
    args = parser.parse_args(argv)

    if args.command in ("console", "demo") and hasattr(sys.stdout, "reconfigure"):
        # Emoji-safe output on older Windows consoles.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.command == "console":
        from .mock_console import run_console

        run_console()
        return 0
    if args.command == "demo":
        from .mock_console import run_demo

        run_demo()
        return 0

    load_dotenv()
    try:
        settings = Settings.from_env()
        logging.basicConfig(
            level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
        if args.command == "run":
            return cmd_run(settings)
        if args.command == "setup-sheet":
            return cmd_setup_sheet(settings, args.admin_id, args.admin_name)
        return cmd_check(settings)
    except ConfigError as exc:
        print(f"Configuration problem:\n{exc}\n\nSee .env.example and README.md.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
