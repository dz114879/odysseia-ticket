from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Sequence

from core.constants import CURRENT_SCHEMA_VERSION, SCHEMA_VERSION_TABLE
from core.errors import DatabaseMigrationError
from db.connection import DatabaseManager


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    operation: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True, slots=True)
class MigrationReport:
    initial_version: int
    final_version: int
    applied_versions: list[int]


def _ensure_schema_version_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_VERSION_TABLE} (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        );
        """
    )
    existing = connection.execute(
        f"SELECT version FROM {SCHEMA_VERSION_TABLE} WHERE id = 1;"
    ).fetchone()
    if existing is None:
        connection.execute(
            f"INSERT INTO {SCHEMA_VERSION_TABLE} (id, version) VALUES (1, 0);"
        )


def _get_current_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        f"SELECT version FROM {SCHEMA_VERSION_TABLE} WHERE id = 1;"
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _set_current_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        f"UPDATE {SCHEMA_VERSION_TABLE} SET version = ? WHERE id = 1;",
        (version,),
    )


def _execute_statements(
    connection: sqlite3.Connection,
    statements: Sequence[str],
) -> None:
    for statement in statements:
        connection.execute(statement)


_MIGRATION_V1_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id TEXT PRIMARY KEY,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER,
        creator_id INTEGER NOT NULL,
        category_key TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        has_user_message INTEGER NOT NULL DEFAULT 0,
        claimed_by INTEGER,
        priority TEXT NOT NULL DEFAULT 'medium'
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tickets_guild_status
        ON tickets (guild_id, status);
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_channel_id
        ON tickets (channel_id)
        WHERE channel_id IS NOT NULL;
    """,
    """
    CREATE TABLE IF NOT EXISTS guild_configs (
        guild_id INTEGER PRIMARY KEY,
        is_initialized INTEGER NOT NULL DEFAULT 0,
        log_channel_id INTEGER,
        archive_channel_id INTEGER,
        ticket_category_channel_id INTEGER,
        admin_role_id INTEGER,
        claim_mode TEXT NOT NULL DEFAULT 'relaxed',
        max_open_tickets INTEGER NOT NULL DEFAULT 100,
        timezone TEXT NOT NULL DEFAULT 'UTC',
        enable_download_window INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS ticket_categories (
        guild_id INTEGER NOT NULL,
        category_key TEXT NOT NULL,
        display_name TEXT NOT NULL,
        emoji TEXT,
        description TEXT,
        staff_role_id INTEGER,
        staff_user_ids_json TEXT NOT NULL DEFAULT '[]',
        extra_welcome_text TEXT,
        is_enabled INTEGER NOT NULL DEFAULT 1,
        allowlist_role_ids_json TEXT NOT NULL DEFAULT '[]',
        denylist_role_ids_json TEXT NOT NULL DEFAULT '[]',
        sort_order INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, category_key)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS panels (
        panel_id TEXT PRIMARY KEY,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        nonce TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_panels_message_id
        ON panels (message_id);
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_panels_one_active_per_guild
        ON panels (guild_id)
        WHERE is_active = 1;
    """,
    """
    CREATE TABLE IF NOT EXISTS ticket_counters (
        guild_id INTEGER NOT NULL,
        category_key TEXT NOT NULL,
        next_number INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (guild_id, category_key)
    );
    """,
)


def _migration_v1_create_base_schema(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V1_STATEMENTS)


def _validate_migration_plan(ordered_migrations: Sequence[Migration]) -> None:
    latest_declared_version = ordered_migrations[-1].version if ordered_migrations else 0
    if latest_declared_version != CURRENT_SCHEMA_VERSION:
        raise DatabaseMigrationError(
            "迁移定义与 CURRENT_SCHEMA_VERSION 不一致。"
        )


MIGRATIONS = [
    Migration(
        version=1,
        name="create_base_schema",
        operation=_migration_v1_create_base_schema,
    ),
]


def apply_migrations(database: DatabaseManager) -> MigrationReport:
    ordered_migrations = sorted(MIGRATIONS, key=lambda item: item.version)
    applied_versions: list[int] = []
    _validate_migration_plan(ordered_migrations)

    try:
        with database.session() as connection:
            _ensure_schema_version_table(connection)
            current_version = _get_current_version(connection)
            initial_version = current_version

            if current_version > CURRENT_SCHEMA_VERSION:
                raise DatabaseMigrationError("数据库 schema 版本高于当前程序支持的版本。")

            for migration in ordered_migrations:
                if migration.version <= current_version:
                    continue

                migration.operation(connection)
                _set_current_version(connection, migration.version)
                applied_versions.append(migration.version)
                current_version = migration.version
    except sqlite3.Error as exc:
        raise DatabaseMigrationError(f"数据库迁移失败: {exc}") from exc

    if current_version != CURRENT_SCHEMA_VERSION:
        raise DatabaseMigrationError("数据库 schema 版本未达到预期。")

    return MigrationReport(
        initial_version=initial_version,
        final_version=current_version,
        applied_versions=applied_versions,
    )
