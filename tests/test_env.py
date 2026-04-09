from __future__ import annotations

from pathlib import Path

import pytest

from config.env import _parse_bool, _parse_optional_int, load_env_settings
from config.static import (
    DEFAULT_BOT_PREFIX,
    DEFAULT_LOG_LEVEL,
    DEFAULT_SCHEDULER_INTERVAL_SECONDS,
    LOG_FILENAME,
    SQLITE_FILENAME,
)
from core.errors import ConfigurationError


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, False),
        ("", False),
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
    ],
)
def test_parse_bool_supports_expected_values(raw_value: str | None, expected: bool) -> None:
    assert _parse_bool(raw_value, default=False) is expected


def test_parse_bool_rejects_invalid_value() -> None:
    with pytest.raises(ConfigurationError, match="无法解析布尔值"):
        _parse_bool("sometimes")


@pytest.mark.parametrize("raw_value", ["abc", "12.5", "1 2"])
def test_parse_optional_int_rejects_invalid_values(raw_value: str) -> None:
    with pytest.raises(ConfigurationError, match="DISCORD_APP_ID 必须是整数"):
        _parse_optional_int(raw_value, field_name="DISCORD_APP_ID")


def test_load_env_settings_uses_defaults_when_values_absent(
    isolated_env_paths: dict[str, Path],
    make_env_file,
) -> None:
    env_file = make_env_file()

    settings = load_env_settings(env_file=env_file)

    assert settings.discord_bot_token == ""
    assert settings.application_id is None
    assert settings.bot_prefix == DEFAULT_BOT_PREFIX
    assert settings.sqlite_path == isolated_env_paths["data_dir"] / SQLITE_FILENAME
    assert settings.log_level == DEFAULT_LOG_LEVEL
    assert settings.log_file == isolated_env_paths["log_dir"] / LOG_FILENAME
    assert settings.scheduler_interval_seconds == DEFAULT_SCHEDULER_INTERVAL_SECONDS
    assert settings.auto_sync_commands is False


def test_load_env_settings_resolves_relative_paths_and_explicit_values(
    isolated_env_paths: dict[str, Path],
    make_env_file,
) -> None:
    env_file = make_env_file(
        DISCORD_BOT_TOKEN="secret-token",
        DISCORD_APP_ID=987654321,
        BOT_PREFIX="#",
        SQLITE_PATH="var/sqlite/runtime.sqlite3",
        LOG_LEVEL="debug",
        LOG_FILE="var/logs/runtime.log",
        SCHEDULER_INTERVAL_SECONDS=15,
        AUTO_SYNC_COMMANDS="yes",
    )

    settings = load_env_settings(env_file=env_file)

    assert settings.discord_bot_token == "secret-token"
    assert settings.application_id == 987654321
    assert settings.bot_prefix == "#"
    assert settings.sqlite_path == isolated_env_paths["base_dir"] / "var" / "sqlite" / "runtime.sqlite3"
    assert settings.log_level == "DEBUG"
    assert settings.log_file == isolated_env_paths["base_dir"] / "var" / "logs" / "runtime.log"
    assert settings.scheduler_interval_seconds == 15
    assert settings.auto_sync_commands is True


@pytest.mark.parametrize(
    ("scheduler_interval_seconds", "error_message"),
    [
        (0, "SCHEDULER_INTERVAL_SECONDS 必须大于 0"),
        (-5, "SCHEDULER_INTERVAL_SECONDS 必须大于 0"),
        ("not-an-int", "SCHEDULER_INTERVAL_SECONDS 必须是整数"),
    ],
)
def test_load_env_settings_validates_scheduler_interval(
    scheduler_interval_seconds: int | str,
    error_message: str,
    make_env_file,
) -> None:
    env_file = make_env_file(SCHEDULER_INTERVAL_SECONDS=scheduler_interval_seconds)

    with pytest.raises(ConfigurationError, match=error_message):
        load_env_settings(env_file=env_file)
