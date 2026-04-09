from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
import sys
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config.env as env_module
from config.env import EnvSettings
from db.connection import DatabaseManager
from db.migrations import apply_migrations

ENV_KEYS = (
    "DISCORD_BOT_TOKEN",
    "DISCORD_APP_ID",
    "BOT_PREFIX",
    "SQLITE_PATH",
    "LOG_LEVEL",
    "LOG_FILE",
    "SCHEDULER_INTERVAL_SECONDS",
    "AUTO_SYNC_COMMANDS",
)


@pytest.fixture(autouse=True)
def clear_ticket_bot_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def isolated_env_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Path]:
    base_dir = tmp_path / "project"
    data_dir = base_dir / "data"
    log_dir = base_dir / "logs"

    monkeypatch.setattr(env_module, "BASE_DIR", base_dir)
    monkeypatch.setattr(env_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(env_module, "LOG_DIR", log_dir)

    return {
        "base_dir": base_dir,
        "data_dir": data_dir,
        "log_dir": log_dir,
    }


@pytest.fixture
def make_env_file(tmp_path: Path) -> Callable[..., Path]:
    def factory(**values: Any) -> Path:
        env_file = tmp_path / ".env"
        lines: list[str] = []
        for key, value in values.items():
            if isinstance(value, bool):
                raw_value = "true" if value else "false"
            elif value is None:
                raw_value = ""
            else:
                raw_value = str(value)
            lines.append(f"{key}={raw_value}")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return env_file

    return factory


@pytest.fixture
def temp_database_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "test.sqlite3"


@pytest.fixture
def database_manager(temp_database_path: Path) -> DatabaseManager:
    return DatabaseManager(temp_database_path)


@pytest.fixture
def migrated_database(database_manager: DatabaseManager) -> DatabaseManager:
    apply_migrations(database_manager)
    return database_manager


@pytest.fixture
def make_settings(tmp_path: Path) -> Callable[..., EnvSettings]:
    def factory(**overrides: Any) -> EnvSettings:
        values: dict[str, Any] = {
            "discord_bot_token": "test-token",
            "application_id": 123456789,
            "bot_prefix": "!",
            "sqlite_path": tmp_path / "data" / "ticket-bot.sqlite3",
            "log_level": "INFO",
            "log_file": tmp_path / "logs" / "ticket-bot.log",
            "scheduler_interval_seconds": 30,
            "auto_sync_commands": False,
        }
        values.update(overrides)
        return EnvSettings(**values)

    return factory
