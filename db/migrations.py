from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from collections.abc import Callable, Sequence

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
    existing = connection.execute(f"SELECT version FROM {SCHEMA_VERSION_TABLE} WHERE id = 1;").fetchone()
    if existing is None:
        connection.execute(f"INSERT INTO {SCHEMA_VERSION_TABLE} (id, version) VALUES (1, 0);")


def _get_current_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(f"SELECT version FROM {SCHEMA_VERSION_TABLE} WHERE id = 1;").fetchone()
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


_MIGRATION_V2_STATEMENTS = ("ALTER TABLE tickets ADD COLUMN last_user_message_at TEXT;",)


_MIGRATION_V3_STATEMENTS = ("ALTER TABLE tickets ADD COLUMN staff_panel_message_id INTEGER;",)


_MIGRATION_V4_STATEMENTS = ("ALTER TABLE tickets ADD COLUMN priority_before_sleep TEXT;",)


_MIGRATION_V5_STATEMENTS = (
    "ALTER TABLE tickets ADD COLUMN status_before TEXT;",
    "ALTER TABLE tickets ADD COLUMN transfer_target_category TEXT;",
    "ALTER TABLE tickets ADD COLUMN transfer_initiated_by INTEGER;",
    "ALTER TABLE tickets ADD COLUMN transfer_reason TEXT;",
    "ALTER TABLE tickets ADD COLUMN transfer_execute_at TEXT;",
    "ALTER TABLE tickets ADD COLUMN transfer_history_json TEXT NOT NULL DEFAULT '[]';",
)


_MIGRATION_V6_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS ticket_mutes (
        ticket_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        muted_by INTEGER NOT NULL,
        reason TEXT,
        expire_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (ticket_id, user_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_ticket_mutes_expire_at ON ticket_mutes (expire_at) WHERE expire_at IS NOT NULL;",
)


_MIGRATION_V7_STATEMENTS = (
    "ALTER TABLE tickets ADD COLUMN close_reason TEXT;",
    "ALTER TABLE tickets ADD COLUMN close_initiated_by INTEGER;",
    "ALTER TABLE tickets ADD COLUMN close_execute_at TEXT;",
    "ALTER TABLE tickets ADD COLUMN closed_at TEXT;",
    "ALTER TABLE tickets ADD COLUMN archive_message_id INTEGER;",
    "ALTER TABLE tickets ADD COLUMN archived_at TEXT;",
    "ALTER TABLE tickets ADD COLUMN message_count INTEGER;",
    "CREATE INDEX IF NOT EXISTS idx_tickets_close_execute_at ON tickets (close_execute_at) WHERE close_execute_at IS NOT NULL;",
)


_MIGRATION_V8_STATEMENTS = ("ALTER TABLE tickets ADD COLUMN snapshot_bootstrapped_at TEXT;",)


_MIGRATION_V9_STATEMENTS = (
    "ALTER TABLE tickets ADD COLUMN queued_at TEXT;",
    """
    CREATE INDEX IF NOT EXISTS idx_tickets_queue_order
        ON tickets (guild_id, queued_at, created_at, ticket_id)
        WHERE status = 'queued';
    """,
)


_MIGRATION_V10_STATEMENTS = (
    "ALTER TABLE tickets ADD COLUMN archive_last_error TEXT;",
    "ALTER TABLE tickets ADD COLUMN archive_attempts INTEGER NOT NULL DEFAULT 0;",
)


def _migration_v1_create_base_schema(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V1_STATEMENTS)


def _migration_v2_add_draft_activity_tracking(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V2_STATEMENTS)


def _migration_v3_add_staff_panel_tracking(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V3_STATEMENTS)


def _migration_v4_add_sleep_priority_tracking(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V4_STATEMENTS)


def _migration_v5_add_transfer_tracking(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V5_STATEMENTS)


def _migration_v6_create_ticket_mutes_table(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V6_STATEMENTS)


def _migration_v7_add_close_archive_tracking(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V7_STATEMENTS)


def _migration_v8_add_snapshot_bootstrap_tracking(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V8_STATEMENTS)


def _migration_v9_add_queue_tracking(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V9_STATEMENTS)


def _migration_v10_add_archive_failure_tracking(connection: sqlite3.Connection) -> None:
    _execute_statements(connection, _MIGRATION_V10_STATEMENTS)


def _validate_migration_plan(ordered_migrations: Sequence[Migration]) -> None:
    latest_declared_version = ordered_migrations[-1].version if ordered_migrations else 0
    if latest_declared_version != CURRENT_SCHEMA_VERSION:
        raise DatabaseMigrationError("迁移定义与 CURRENT_SCHEMA_VERSION 不一致。")


MIGRATIONS = [
    Migration(
        version=1,
        name="create_base_schema",
        operation=_migration_v1_create_base_schema,
    ),
    Migration(
        version=2,
        name="add_draft_activity_tracking",
        operation=_migration_v2_add_draft_activity_tracking,
    ),
    Migration(
        version=3,
        name="add_staff_panel_tracking",
        operation=_migration_v3_add_staff_panel_tracking,
    ),
    Migration(
        version=4,
        name="add_sleep_priority_tracking",
        operation=_migration_v4_add_sleep_priority_tracking,
    ),
    Migration(
        version=5,
        name="add_transfer_tracking",
        operation=_migration_v5_add_transfer_tracking,
    ),
    Migration(
        version=6,
        name="create_ticket_mutes_table",
        operation=_migration_v6_create_ticket_mutes_table,
    ),
    Migration(
        version=7,
        name="add_close_archive_tracking",
        operation=_migration_v7_add_close_archive_tracking,
    ),
    Migration(
        version=8,
        name="add_snapshot_bootstrap_tracking",
        operation=_migration_v8_add_snapshot_bootstrap_tracking,
    ),
    Migration(
        version=9,
        name="add_queue_tracking",
        operation=_migration_v9_add_queue_tracking,
    ),
    Migration(
        version=10,
        name="add_archive_failure_tracking",
        operation=_migration_v10_add_archive_failure_tracking,
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
