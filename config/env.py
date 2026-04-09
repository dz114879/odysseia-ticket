from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from config.static import (
    BASE_DIR,
    DATA_DIR,
    DEFAULT_BOT_PREFIX,
    DEFAULT_LOG_LEVEL,
    DEFAULT_SCHEDULER_INTERVAL_SECONDS,
    LOG_DIR,
    LOG_FILENAME,
    SQLITE_FILENAME,
)
from core.errors import ConfigurationError


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class EnvSettings:
    discord_bot_token: str
    application_id: int | None
    bot_prefix: str
    sqlite_path: Path
    log_level: str
    log_file: Path
    scheduler_interval_seconds: int
    auto_sync_commands: bool


def _parse_bool(raw_value: str | None, *, default: bool = False) -> bool:
    if raw_value is None or raw_value.strip() == "":
        return default

    normalized = raw_value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False

    raise ConfigurationError(f"无法解析布尔值: {raw_value!r}")


def _parse_optional_int(raw_value: str | None, *, field_name: str) -> int | None:
    if raw_value is None or raw_value.strip() == "":
        return None

    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{field_name} 必须是整数。") from exc


def _resolve_path(raw_value: str | None, *, default: Path) -> Path:
    candidate = Path(raw_value).expanduser() if raw_value else default
    if not candidate.is_absolute():
        candidate = (BASE_DIR / candidate).resolve()
    return candidate


def load_env_settings(env_file: Path | None = None) -> EnvSettings:
    load_dotenv(dotenv_path=env_file or BASE_DIR / ".env", override=False)

    try:
        scheduler_interval_seconds = int(
            os.getenv(
                "SCHEDULER_INTERVAL_SECONDS",
                str(DEFAULT_SCHEDULER_INTERVAL_SECONDS),
            )
        )
    except ValueError as exc:
        raise ConfigurationError("SCHEDULER_INTERVAL_SECONDS 必须是整数。") from exc

    if scheduler_interval_seconds <= 0:
        raise ConfigurationError("SCHEDULER_INTERVAL_SECONDS 必须大于 0。")

    sqlite_path = _resolve_path(
        os.getenv("SQLITE_PATH"),
        default=DATA_DIR / SQLITE_FILENAME,
    )
    log_file = _resolve_path(
        os.getenv("LOG_FILE"),
        default=LOG_DIR / LOG_FILENAME,
    )

    return EnvSettings(
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", "").strip(),
        application_id=_parse_optional_int(
            os.getenv("DISCORD_APP_ID"),
            field_name="DISCORD_APP_ID",
        ),
        bot_prefix=os.getenv("BOT_PREFIX", DEFAULT_BOT_PREFIX).strip() or DEFAULT_BOT_PREFIX,
        sqlite_path=sqlite_path,
        log_level=os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper() or DEFAULT_LOG_LEVEL,
        log_file=log_file,
        scheduler_interval_seconds=scheduler_interval_seconds,
        auto_sync_commands=_parse_bool(
            os.getenv("AUTO_SYNC_COMMANDS"),
            default=False,
        ),
    )
