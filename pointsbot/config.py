"""Settings loaded from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    pass


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    mock: bool
    deduct_bot_token: str
    points_bot_token: str
    spreadsheet_id: str
    service_account_file: Path
    timezone: ZoneInfo
    max_amount: int
    pending_ttl_seconds: int
    users_cache_seconds: int
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        tz_name = env.get("TIMEZONE", "").strip() or "Europe/Vilnius"
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"Unknown TIMEZONE {tz_name!r} (example: Europe/Vilnius)") from None
        return cls(
            mock=_bool(env.get("MOCK")),
            deduct_bot_token=env.get("DEDUCT_BOT_TOKEN", "").strip(),
            points_bot_token=env.get("POINTS_BOT_TOKEN", "").strip(),
            spreadsheet_id=env.get("SPREADSHEET_ID", "").strip(),
            service_account_file=Path(env.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip() or "service_account.json"),
            timezone=tz,
            max_amount=_int(env, "MAX_AMOUNT", 100_000),
            pending_ttl_seconds=_int(env, "CONFIRM_TIMEOUT_SECONDS", 300),
            users_cache_seconds=_int(env, "USERS_CACHE_SECONDS", 30),
            log_level=(env.get("LOG_LEVEL", "").strip() or "INFO").upper(),
        )

    def sheet_problems(self) -> list[str]:
        problems = []
        if not self.spreadsheet_id:
            problems.append("SPREADSHEET_ID is empty (the long ID from the sheet URL).")
        if not self.service_account_file.is_file():
            problems.append(f"Service account key not found: {self.service_account_file}")
        return problems

    def telegram_problems(self) -> list[str]:
        problems = []
        if not self.deduct_bot_token:
            problems.append("DEDUCT_BOT_TOKEN is empty (token of bot #1 from @BotFather).")
        if not self.points_bot_token:
            problems.append("POINTS_BOT_TOKEN is empty (token of bot #2 from @BotFather).")
        if self.deduct_bot_token and self.deduct_bot_token == self.points_bot_token:
            problems.append("DEDUCT_BOT_TOKEN and POINTS_BOT_TOKEN must be two different bots.")
        return problems
