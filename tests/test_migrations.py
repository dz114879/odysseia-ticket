from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import db.migrations as migrations_module
from core.constants import CURRENT_SCHEMA_VERSION, SCHEMA_VERSION_TABLE
from core.errors import DatabaseMigrationError
from db.connection import DatabaseManager
from db.migrations import Migration, apply_migrations


EXPECTED_TABLES = {
    "tickets",
    "guild_configs",
    "ticket_categories",
    "panels",
    "ticket_mutes",
    "ticket_counters",
    SCHEMA_VERSION_TABLE,
}


def test_apply_migrations_initializes_empty_database(database_manager) -> None:
    report = apply_migrations(database_manager)

    assert report.initial_version == 0
    assert report.final_version == CURRENT_SCHEMA_VERSION
    assert report.applied_versions == list(range(1, CURRENT_SCHEMA_VERSION + 1))

    rows = database_manager.fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table';",
    )
    table_names = {row["name"] for row in rows}
    assert EXPECTED_TABLES.issubset(table_names)

    version_row = database_manager.fetchone(
        f"SELECT version FROM {SCHEMA_VERSION_TABLE} WHERE id = 1;",
    )
    assert version_row is not None
    assert version_row["version"] == CURRENT_SCHEMA_VERSION

    ticket_columns = database_manager.fetchall("PRAGMA table_info(tickets);")
    ticket_column_names = {row["name"] for row in ticket_columns}
    assert "last_user_message_at" in ticket_column_names
    assert "staff_panel_message_id" in ticket_column_names
    assert "priority_before_sleep" in ticket_column_names
    assert "status_before" in ticket_column_names
    assert "transfer_target_category" in ticket_column_names
    assert "transfer_initiated_by" in ticket_column_names
    assert "transfer_reason" in ticket_column_names
    assert "transfer_execute_at" in ticket_column_names
    assert "transfer_history_json" in ticket_column_names
    assert "close_reason" in ticket_column_names
    assert "close_initiated_by" in ticket_column_names
    assert "close_execute_at" in ticket_column_names
    assert "closed_at" in ticket_column_names
    assert "archive_message_id" in ticket_column_names
    assert "archived_at" in ticket_column_names
    assert "message_count" in ticket_column_names
    assert "snapshot_bootstrapped_at" in ticket_column_names
    assert "archive_last_error" in ticket_column_names
    assert "archive_attempts" in ticket_column_names
    assert "queued_at" in ticket_column_names

    # V13: runtime config columns in guild_configs
    gc_columns = database_manager.fetchall("PRAGMA table_info(guild_configs);")
    gc_column_names = {row["name"] for row in gc_columns}
    v13_expected_columns = {
        # Numeric
        "draft_inactive_close_hours",
        "draft_abandon_timeout_hours",
        "transfer_delay_seconds",
        "close_revoke_window_seconds",
        "close_request_timeout_seconds",
        "snapshot_warning_threshold",
        "snapshot_limit",
        # Text (nullable)
        "panel_title",
        "panel_description",
        "panel_bullet_points",
        "panel_footer_text",
        "draft_welcome_text",
        "snapshot_warning_text",
        "snapshot_limit_text",
        "close_request_text",
        "closing_notice_text",
        "close_revoke_text",
    }
    assert v13_expected_columns.issubset(gc_column_names), (
        f"Missing guild_configs columns from V13 migration: {v13_expected_columns - gc_column_names}"
    )


def test_apply_migrations_is_idempotent(database_manager) -> None:
    first_report = apply_migrations(database_manager)
    second_report = apply_migrations(database_manager)

    assert first_report.applied_versions == list(range(1, CURRENT_SCHEMA_VERSION + 1))
    assert second_report.initial_version == CURRENT_SCHEMA_VERSION
    assert second_report.final_version == CURRENT_SCHEMA_VERSION
    assert second_report.applied_versions == []

    version_rows = database_manager.fetchall(
        f"SELECT version FROM {SCHEMA_VERSION_TABLE};",
    )
    assert len(version_rows) == 1
    assert version_rows[0]["version"] == CURRENT_SCHEMA_VERSION


def test_apply_migrations_wraps_sqlite_errors(
    database_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_operation(connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(
        migrations_module,
        "MIGRATIONS",
        [Migration(version=1, name="broken", operation=broken_operation)],
    )
    monkeypatch.setattr(
        migrations_module,
        "CURRENT_SCHEMA_VERSION",
        1,
    )

    with pytest.raises(DatabaseMigrationError, match="数据库迁移失败"):
        apply_migrations(database_manager)


def test_apply_migrations_rolls_back_partial_changes_on_failure(
    database_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def partially_failing_operation(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE rollback_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL);")
        raise sqlite3.OperationalError("boom after partial change")

    monkeypatch.setattr(
        migrations_module,
        "MIGRATIONS",
        [Migration(version=1, name="partial_failure", operation=partially_failing_operation)],
    )
    monkeypatch.setattr(
        migrations_module,
        "CURRENT_SCHEMA_VERSION",
        1,
    )

    with pytest.raises(DatabaseMigrationError, match="数据库迁移失败"):
        apply_migrations(database_manager)

    rows = database_manager.fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table';",
    )
    table_names = {row["name"] for row in rows}
    assert "rollback_probe" not in table_names
    assert SCHEMA_VERSION_TABLE not in table_names


def test_apply_migrations_fails_when_schema_version_drift_detected(
    temp_database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_manager = DatabaseManager(temp_database_path)
    monkeypatch.setattr(
        migrations_module,
        "CURRENT_SCHEMA_VERSION",
        CURRENT_SCHEMA_VERSION + 1,
    )

    with pytest.raises(
        DatabaseMigrationError,
        match="迁移定义与 CURRENT_SCHEMA_VERSION 不一致",
    ):
        apply_migrations(database_manager)

    rows = database_manager.fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table';",
    )
    table_names = {row["name"] for row in rows}
    assert SCHEMA_VERSION_TABLE not in table_names
