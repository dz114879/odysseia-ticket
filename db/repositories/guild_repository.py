from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from core.enums import ClaimMode
from core.models import GuildConfigRecord, TicketCategoryConfig
from db.repositories.base import (
    UNSET,
    BaseRepository,
    build_update_set_clause,
    from_db_bool,
    to_db_bool,
    utc_now_iso,
)


class GuildRepository(BaseRepository):
    def _row_to_guild_config(self, row: sqlite3.Row) -> GuildConfigRecord:
        return GuildConfigRecord(
            guild_id=row["guild_id"],
            is_initialized=from_db_bool(row["is_initialized"]),
            log_channel_id=row["log_channel_id"],
            archive_channel_id=row["archive_channel_id"],
            ticket_category_channel_id=row["ticket_category_channel_id"],
            admin_role_id=row["admin_role_id"],
            claim_mode=ClaimMode(row["claim_mode"]),
            max_open_tickets=row["max_open_tickets"],
            timezone=row["timezone"],
            enable_download_window=from_db_bool(row["enable_download_window"]),
            draft_inactive_close_hours=row["draft_inactive_close_hours"],
            draft_abandon_timeout_hours=row["draft_abandon_timeout_hours"],
            transfer_delay_seconds=row["transfer_delay_seconds"],
            close_revoke_window_seconds=row["close_revoke_window_seconds"],
            close_request_timeout_seconds=row["close_request_timeout_seconds"],
            snapshot_warning_threshold=row["snapshot_warning_threshold"],
            snapshot_limit=row["snapshot_limit"],
            panel_title=row["panel_title"],
            panel_description=row["panel_description"],
            panel_bullet_points=row["panel_bullet_points"],
            panel_footer_text=row["panel_footer_text"],
            draft_welcome_text=row["draft_welcome_text"],
            snapshot_warning_text=row["snapshot_warning_text"],
            snapshot_limit_text=row["snapshot_limit_text"],
            close_request_text=row["close_request_text"],
            closing_notice_text=row["closing_notice_text"],
            close_revoke_text=row["close_revoke_text"],
            updated_at=row["updated_at"],
        )

    def _row_to_category_config(self, row: sqlite3.Row) -> TicketCategoryConfig:
        return TicketCategoryConfig(
            guild_id=row["guild_id"],
            category_key=row["category_key"],
            display_name=row["display_name"],
            emoji=row["emoji"],
            description=row["description"],
            staff_role_ids_json=row["staff_role_ids_json"],
            staff_user_ids_json=row["staff_user_ids_json"],
            is_enabled=from_db_bool(row["is_enabled"]),
            allowlist_role_ids_json=row["allowlist_role_ids_json"],
            denylist_role_ids_json=row["denylist_role_ids_json"],
            sort_order=row["sort_order"],
        )

    def get_config(
        self,
        guild_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> GuildConfigRecord | None:
        with self.read_connection(connection) as current_connection:
            row = current_connection.execute(
                "SELECT * FROM guild_configs WHERE guild_id = ?;",
                (guild_id,),
            ).fetchone()
        return self._row_to_guild_config(row) if row is not None else None

    def upsert_config(
        self,
        record: GuildConfigRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> GuildConfigRecord:
        updated_at = record.updated_at or utc_now_iso()

        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                """
                INSERT INTO guild_configs (
                    guild_id,
                    is_initialized,
                    log_channel_id,
                    archive_channel_id,
                    ticket_category_channel_id,
                    admin_role_id,
                    claim_mode,
                    max_open_tickets,
                    timezone,
                    enable_download_window,
                    draft_inactive_close_hours,
                    draft_abandon_timeout_hours,
                    transfer_delay_seconds,
                    close_revoke_window_seconds,
                    close_request_timeout_seconds,
                    snapshot_warning_threshold,
                    snapshot_limit,
                    panel_title,
                    panel_description,
                    panel_bullet_points,
                    panel_footer_text,
                    draft_welcome_text,
                    snapshot_warning_text,
                    snapshot_limit_text,
                    close_request_text,
                    closing_notice_text,
                    close_revoke_text,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    is_initialized = excluded.is_initialized,
                    log_channel_id = excluded.log_channel_id,
                    archive_channel_id = excluded.archive_channel_id,
                    ticket_category_channel_id = excluded.ticket_category_channel_id,
                    admin_role_id = excluded.admin_role_id,
                    claim_mode = excluded.claim_mode,
                    max_open_tickets = excluded.max_open_tickets,
                    timezone = excluded.timezone,
                    enable_download_window = excluded.enable_download_window,
                    draft_inactive_close_hours = excluded.draft_inactive_close_hours,
                    draft_abandon_timeout_hours = excluded.draft_abandon_timeout_hours,
                    transfer_delay_seconds = excluded.transfer_delay_seconds,
                    close_revoke_window_seconds = excluded.close_revoke_window_seconds,
                    close_request_timeout_seconds = excluded.close_request_timeout_seconds,
                    snapshot_warning_threshold = excluded.snapshot_warning_threshold,
                    snapshot_limit = excluded.snapshot_limit,
                    panel_title = excluded.panel_title,
                    panel_description = excluded.panel_description,
                    panel_bullet_points = excluded.panel_bullet_points,
                    panel_footer_text = excluded.panel_footer_text,
                    draft_welcome_text = excluded.draft_welcome_text,
                    snapshot_warning_text = excluded.snapshot_warning_text,
                    snapshot_limit_text = excluded.snapshot_limit_text,
                    close_request_text = excluded.close_request_text,
                    closing_notice_text = excluded.closing_notice_text,
                    close_revoke_text = excluded.close_revoke_text,
                    updated_at = excluded.updated_at;
                """,
                (
                    record.guild_id,
                    to_db_bool(record.is_initialized),
                    record.log_channel_id,
                    record.archive_channel_id,
                    record.ticket_category_channel_id,
                    record.admin_role_id,
                    record.claim_mode.value,
                    record.max_open_tickets,
                    record.timezone,
                    to_db_bool(record.enable_download_window),
                    record.draft_inactive_close_hours,
                    record.draft_abandon_timeout_hours,
                    record.transfer_delay_seconds,
                    record.close_revoke_window_seconds,
                    record.close_request_timeout_seconds,
                    record.snapshot_warning_threshold,
                    record.snapshot_limit,
                    record.panel_title,
                    record.panel_description,
                    record.panel_bullet_points,
                    record.panel_footer_text,
                    record.draft_welcome_text,
                    record.snapshot_warning_text,
                    record.snapshot_limit_text,
                    record.close_request_text,
                    record.closing_notice_text,
                    record.close_revoke_text,
                    updated_at,
                ),
            )
        return self.get_config(record.guild_id, connection=connection) or GuildConfigRecord(
            guild_id=record.guild_id,
            is_initialized=record.is_initialized,
            log_channel_id=record.log_channel_id,
            archive_channel_id=record.archive_channel_id,
            ticket_category_channel_id=record.ticket_category_channel_id,
            admin_role_id=record.admin_role_id,
            claim_mode=record.claim_mode,
            max_open_tickets=record.max_open_tickets,
            timezone=record.timezone,
            enable_download_window=record.enable_download_window,
            updated_at=updated_at,
        )

    def update_config(
        self,
        guild_id: int,
        *,
        is_initialized: bool | object = UNSET,
        log_channel_id: int | None | object = UNSET,
        archive_channel_id: int | None | object = UNSET,
        ticket_category_channel_id: int | None | object = UNSET,
        admin_role_id: int | None | object = UNSET,
        claim_mode: ClaimMode | object = UNSET,
        max_open_tickets: int | object = UNSET,
        timezone: str | object = UNSET,
        enable_download_window: bool | object = UNSET,
        draft_inactive_close_hours: int | object = UNSET,
        draft_abandon_timeout_hours: int | object = UNSET,
        transfer_delay_seconds: int | object = UNSET,
        close_revoke_window_seconds: int | object = UNSET,
        close_request_timeout_seconds: int | object = UNSET,
        snapshot_warning_threshold: int | object = UNSET,
        snapshot_limit: int | object = UNSET,
        panel_title: str | None | object = UNSET,
        panel_description: str | None | object = UNSET,
        panel_bullet_points: str | None | object = UNSET,
        panel_footer_text: str | None | object = UNSET,
        draft_welcome_text: str | None | object = UNSET,
        snapshot_warning_text: str | None | object = UNSET,
        snapshot_limit_text: str | None | object = UNSET,
        close_request_text: str | None | object = UNSET,
        closing_notice_text: str | None | object = UNSET,
        close_revoke_text: str | None | object = UNSET,
        updated_at: str | object = UNSET,
        connection: sqlite3.Connection | None = None,
    ) -> GuildConfigRecord | None:
        updates: dict[str, object] = {}

        if is_initialized is not UNSET:
            updates["is_initialized"] = to_db_bool(bool(is_initialized))
        if log_channel_id is not UNSET:
            updates["log_channel_id"] = log_channel_id
        if archive_channel_id is not UNSET:
            updates["archive_channel_id"] = archive_channel_id
        if ticket_category_channel_id is not UNSET:
            updates["ticket_category_channel_id"] = ticket_category_channel_id
        if admin_role_id is not UNSET:
            updates["admin_role_id"] = admin_role_id
        if claim_mode is not UNSET:
            updates["claim_mode"] = claim_mode.value
        if max_open_tickets is not UNSET:
            updates["max_open_tickets"] = max_open_tickets
        if timezone is not UNSET:
            updates["timezone"] = timezone
        if enable_download_window is not UNSET:
            updates["enable_download_window"] = to_db_bool(bool(enable_download_window))
        if draft_inactive_close_hours is not UNSET:
            updates["draft_inactive_close_hours"] = draft_inactive_close_hours
        if draft_abandon_timeout_hours is not UNSET:
            updates["draft_abandon_timeout_hours"] = draft_abandon_timeout_hours
        if transfer_delay_seconds is not UNSET:
            updates["transfer_delay_seconds"] = transfer_delay_seconds
        if close_revoke_window_seconds is not UNSET:
            updates["close_revoke_window_seconds"] = close_revoke_window_seconds
        if close_request_timeout_seconds is not UNSET:
            updates["close_request_timeout_seconds"] = close_request_timeout_seconds
        if snapshot_warning_threshold is not UNSET:
            updates["snapshot_warning_threshold"] = snapshot_warning_threshold
        if snapshot_limit is not UNSET:
            updates["snapshot_limit"] = snapshot_limit
        if panel_title is not UNSET:
            updates["panel_title"] = panel_title
        if panel_description is not UNSET:
            updates["panel_description"] = panel_description
        if panel_bullet_points is not UNSET:
            updates["panel_bullet_points"] = panel_bullet_points
        if panel_footer_text is not UNSET:
            updates["panel_footer_text"] = panel_footer_text
        if draft_welcome_text is not UNSET:
            updates["draft_welcome_text"] = draft_welcome_text
        if snapshot_warning_text is not UNSET:
            updates["snapshot_warning_text"] = snapshot_warning_text
        if snapshot_limit_text is not UNSET:
            updates["snapshot_limit_text"] = snapshot_limit_text
        if close_request_text is not UNSET:
            updates["close_request_text"] = close_request_text
        if closing_notice_text is not UNSET:
            updates["closing_notice_text"] = closing_notice_text
        if close_revoke_text is not UNSET:
            updates["close_revoke_text"] = close_revoke_text

        if updated_at is not UNSET:
            updates["updated_at"] = updated_at
        elif updates:
            updates["updated_at"] = utc_now_iso()

        if not updates:
            return self.get_config(guild_id, connection=connection)

        set_clause, parameters = build_update_set_clause(updates)
        parameters.append(guild_id)

        with self.write_connection(connection) as current_connection:
            cursor = current_connection.execute(
                f"UPDATE guild_configs SET {set_clause} WHERE guild_id = ?;",
                parameters,
            )
            if cursor.rowcount == 0:
                return None

        return self.get_config(guild_id, connection=connection)

    def get_category(
        self,
        guild_id: int,
        category_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> TicketCategoryConfig | None:
        with self.read_connection(connection) as current_connection:
            row = current_connection.execute(
                """
                SELECT *
                FROM ticket_categories
                WHERE guild_id = ? AND category_key = ?;
                """,
                (guild_id, category_key),
            ).fetchone()
        return self._row_to_category_config(row) if row is not None else None

    def list_categories(
        self,
        guild_id: int,
        *,
        enabled_only: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> list[TicketCategoryConfig]:
        query = "SELECT * FROM ticket_categories WHERE guild_id = ?"
        parameters: list[object] = [guild_id]
        if enabled_only:
            query += " AND is_enabled = 1"
        query += " ORDER BY sort_order ASC, category_key ASC;"

        with self.read_connection(connection) as current_connection:
            rows = current_connection.execute(query, parameters).fetchall()
        return [self._row_to_category_config(row) for row in rows]

    def upsert_category(
        self,
        record: TicketCategoryConfig,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> TicketCategoryConfig:
        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                """
                INSERT INTO ticket_categories (
                    guild_id,
                    category_key,
                    display_name,
                    emoji,
                    description,
                    staff_role_ids_json,
                    staff_user_ids_json,
                    is_enabled,
                    allowlist_role_ids_json,
                    denylist_role_ids_json,
                    sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, category_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    emoji = excluded.emoji,
                    description = excluded.description,
                    staff_role_ids_json = excluded.staff_role_ids_json,
                    staff_user_ids_json = excluded.staff_user_ids_json,
                    is_enabled = excluded.is_enabled,
                    allowlist_role_ids_json = excluded.allowlist_role_ids_json,
                    denylist_role_ids_json = excluded.denylist_role_ids_json,
                    sort_order = excluded.sort_order;
                """,
                (
                    record.guild_id,
                    record.category_key,
                    record.display_name,
                    record.emoji,
                    record.description,
                    record.staff_role_ids_json,
                    record.staff_user_ids_json,
                    to_db_bool(record.is_enabled),
                    record.allowlist_role_ids_json,
                    record.denylist_role_ids_json,
                    record.sort_order,
                ),
            )
        return self.get_category(record.guild_id, record.category_key, connection=connection) or record

    def replace_categories(
        self,
        guild_id: int,
        categories: Sequence[TicketCategoryConfig],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[TicketCategoryConfig]:
        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                "DELETE FROM ticket_categories WHERE guild_id = ?;",
                (guild_id,),
            )
            for category in categories:
                current_connection.execute(
                    """
                    INSERT INTO ticket_categories (
                        guild_id,
                        category_key,
                        display_name,
                        emoji,
                        description,
                        staff_role_ids_json,
                        staff_user_ids_json,
                        is_enabled,
                        allowlist_role_ids_json,
                        denylist_role_ids_json,
                        sort_order
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        guild_id,
                        category.category_key,
                        category.display_name,
                        category.emoji,
                        category.description,
                        category.staff_role_ids_json,
                        category.staff_user_ids_json,
                        to_db_bool(category.is_enabled),
                        category.allowlist_role_ids_json,
                        category.denylist_role_ids_json,
                        category.sort_order,
                    ),
                )

        return self.list_categories(guild_id, connection=connection)

    def delete_category(
        self,
        guild_id: int,
        category_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        with self.write_connection(connection) as current_connection:
            cursor = current_connection.execute(
                "DELETE FROM ticket_categories WHERE guild_id = ? AND category_key = ?;",
                (guild_id, category_key),
            )
            return cursor.rowcount > 0
