from __future__ import annotations

import sqlite3
from typing import Sequence

from core.enums import TicketPriority, TicketStatus
from core.models import TicketRecord
from db.repositories.base import (
    UNSET,
    BaseRepository,
    build_update_set_clause,
    from_db_bool,
    to_db_bool,
    utc_now_iso,
)


class TicketRepository(BaseRepository):
    def _row_to_record(self, row: sqlite3.Row) -> TicketRecord:
        return TicketRecord(
            ticket_id=row["ticket_id"],
            guild_id=row["guild_id"],
            creator_id=row["creator_id"],
            category_key=row["category_key"],
            channel_id=row["channel_id"],
            status=TicketStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            has_user_message=from_db_bool(row["has_user_message"]),
            last_user_message_at=row["last_user_message_at"],
            claimed_by=row["claimed_by"],
            priority=TicketPriority(row["priority"]),
            priority_before_sleep=(
                TicketPriority(row["priority_before_sleep"])
                if row["priority_before_sleep"]
                else None
            ),
            status_before=TicketStatus(row["status_before"]) if row["status_before"] else None,
            transfer_target_category=row["transfer_target_category"],
            transfer_initiated_by=row["transfer_initiated_by"],
            transfer_reason=row["transfer_reason"],
            transfer_execute_at=row["transfer_execute_at"],
            transfer_history_json=row["transfer_history_json"],
            staff_panel_message_id=row["staff_panel_message_id"],
        )

    def create(
        self,
        record: TicketRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> TicketRecord:
        created_at = record.created_at or utc_now_iso()
        updated_at = record.updated_at or created_at

        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                """
                INSERT INTO tickets (
                    ticket_id,
                    guild_id,
                    channel_id,
                    creator_id,
                    category_key,
                    status,
                    created_at,
                    updated_at,
                    has_user_message,
                    last_user_message_at,
                    claimed_by,
                    priority,
                    priority_before_sleep,
                    status_before,
                    transfer_target_category,
                    transfer_initiated_by,
                    transfer_reason,
                    transfer_execute_at,
                    transfer_history_json,
                    staff_panel_message_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.ticket_id,
                    record.guild_id,
                    record.channel_id,
                    record.creator_id,
                    record.category_key,
                    record.status.value,
                    created_at,
                    updated_at,
                    to_db_bool(record.has_user_message),
                    record.last_user_message_at,
                    record.claimed_by,
                    record.priority.value,
                    record.priority_before_sleep.value if record.priority_before_sleep is not None else None,
                    record.status_before.value if record.status_before is not None else None,
                    record.transfer_target_category,
                    record.transfer_initiated_by,
                    record.transfer_reason,
                    record.transfer_execute_at,
                    record.transfer_history_json,
                    record.staff_panel_message_id,
                ),
            )
        return TicketRecord(
            ticket_id=record.ticket_id,
            guild_id=record.guild_id,
            creator_id=record.creator_id,
            category_key=record.category_key,
            channel_id=record.channel_id,
            status=record.status,
            created_at=created_at,
            updated_at=updated_at,
            has_user_message=record.has_user_message,
            last_user_message_at=record.last_user_message_at,
            claimed_by=record.claimed_by,
            priority=record.priority,
            priority_before_sleep=record.priority_before_sleep,
            status_before=record.status_before,
            transfer_target_category=record.transfer_target_category,
            transfer_initiated_by=record.transfer_initiated_by,
            transfer_reason=record.transfer_reason,
            transfer_execute_at=record.transfer_execute_at,
            transfer_history_json=record.transfer_history_json,
            staff_panel_message_id=record.staff_panel_message_id,
        )

    def upsert(
        self,
        record: TicketRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> TicketRecord:
        created_at = record.created_at or utc_now_iso()
        updated_at = record.updated_at or created_at

        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                """
                INSERT INTO tickets (
                    ticket_id,
                    guild_id,
                    channel_id,
                    creator_id,
                    category_key,
                    status,
                    created_at,
                    updated_at,
                    has_user_message,
                    last_user_message_at,
                    claimed_by,
                    priority,
                    priority_before_sleep,
                    status_before,
                    transfer_target_category,
                    transfer_initiated_by,
                    transfer_reason,
                    transfer_execute_at,
                    transfer_history_json,
                    staff_panel_message_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticket_id) DO UPDATE SET
                    guild_id = excluded.guild_id,
                    channel_id = excluded.channel_id,
                    creator_id = excluded.creator_id,
                    category_key = excluded.category_key,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    has_user_message = excluded.has_user_message,
                    last_user_message_at = excluded.last_user_message_at,
                    claimed_by = excluded.claimed_by,
                    priority = excluded.priority,
                    priority_before_sleep = excluded.priority_before_sleep,
                    status_before = excluded.status_before,
                    transfer_target_category = excluded.transfer_target_category,
                    transfer_initiated_by = excluded.transfer_initiated_by,
                    transfer_reason = excluded.transfer_reason,
                    transfer_execute_at = excluded.transfer_execute_at,
                    transfer_history_json = excluded.transfer_history_json,
                    staff_panel_message_id = excluded.staff_panel_message_id;
                """,
                (
                    record.ticket_id,
                    record.guild_id,
                    record.channel_id,
                    record.creator_id,
                    record.category_key,
                    record.status.value,
                    created_at,
                    updated_at,
                    to_db_bool(record.has_user_message),
                    record.last_user_message_at,
                    record.claimed_by,
                    record.priority.value,
                    record.priority_before_sleep.value if record.priority_before_sleep is not None else None,
                    record.status_before.value if record.status_before is not None else None,
                    record.transfer_target_category,
                    record.transfer_initiated_by,
                    record.transfer_reason,
                    record.transfer_execute_at,
                    record.transfer_history_json,
                    record.staff_panel_message_id,
                ),
            )
        return self.get_by_ticket_id(record.ticket_id, connection=connection) or TicketRecord(
            ticket_id=record.ticket_id,
            guild_id=record.guild_id,
            creator_id=record.creator_id,
            category_key=record.category_key,
            channel_id=record.channel_id,
            status=record.status,
            created_at=created_at,
            updated_at=updated_at,
            has_user_message=record.has_user_message,
            last_user_message_at=record.last_user_message_at,
            claimed_by=record.claimed_by,
            priority=record.priority,
            priority_before_sleep=record.priority_before_sleep,
            status_before=record.status_before,
            transfer_target_category=record.transfer_target_category,
            transfer_initiated_by=record.transfer_initiated_by,
            transfer_reason=record.transfer_reason,
            transfer_execute_at=record.transfer_execute_at,
            transfer_history_json=record.transfer_history_json,
            staff_panel_message_id=record.staff_panel_message_id,
        )

    def get_by_ticket_id(
        self,
        ticket_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> TicketRecord | None:
        with self.read_connection(connection) as current_connection:
            row = current_connection.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?;",
                (ticket_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def get_by_channel_id(
        self,
        channel_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> TicketRecord | None:
        with self.read_connection(connection) as current_connection:
            row = current_connection.execute(
                "SELECT * FROM tickets WHERE channel_id = ?;",
                (channel_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_by_guild(
        self,
        guild_id: int,
        *,
        statuses: Sequence[TicketStatus] | None = None,
        creator_id: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[TicketRecord]:
        clauses = ["guild_id = ?"]
        parameters: list[object] = [guild_id]

        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            parameters.extend(status.value for status in statuses)

        if creator_id is not None:
            clauses.append("creator_id = ?")
            parameters.append(creator_id)

        query = (
            "SELECT * FROM tickets "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at ASC;"
        )
        with self.read_connection(connection) as current_connection:
            rows = current_connection.execute(query, parameters).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_by_statuses(
        self,
        statuses: Sequence[TicketStatus],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[TicketRecord]:
        if not statuses:
            return []

        placeholders = ", ".join("?" for _ in statuses)
        parameters = [status.value for status in statuses]
        query = (
            "SELECT * FROM tickets "
            f"WHERE status IN ({placeholders}) "
            "ORDER BY created_at ASC;"
        )

        with self.read_connection(connection) as current_connection:
            rows = current_connection.execute(query, parameters).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_due_transfer_executions(
        self,
        execute_before: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[TicketRecord]:
        query = (
            "SELECT * FROM tickets "
            "WHERE status = ? "
            "AND transfer_execute_at IS NOT NULL "
            "AND transfer_execute_at <= ? "
            "ORDER BY transfer_execute_at ASC, created_at ASC;"
        )
        parameters = [TicketStatus.TRANSFERRING.value, execute_before]

        with self.read_connection(connection) as current_connection:
            rows = current_connection.execute(query, parameters).fetchall()
        return [self._row_to_record(row) for row in rows]

    def update(
        self,
        ticket_id: str,
        *,
        guild_id: int | object = UNSET,
        channel_id: int | None | object = UNSET,
        creator_id: int | object = UNSET,
        category_key: str | object = UNSET,
        status: TicketStatus | object = UNSET,
        created_at: str | object = UNSET,
        updated_at: str | object = UNSET,
        has_user_message: bool | object = UNSET,
        last_user_message_at: str | None | object = UNSET,
        claimed_by: int | None | object = UNSET,
        priority: TicketPriority | object = UNSET,
        priority_before_sleep: TicketPriority | None | object = UNSET,
        status_before: TicketStatus | None | object = UNSET,
        transfer_target_category: str | None | object = UNSET,
        transfer_initiated_by: int | None | object = UNSET,
        transfer_reason: str | None | object = UNSET,
        transfer_execute_at: str | None | object = UNSET,
        transfer_history_json: str | object = UNSET,
        staff_panel_message_id: int | None | object = UNSET,
        connection: sqlite3.Connection | None = None,
    ) -> TicketRecord | None:
        updates: dict[str, object] = {}

        if guild_id is not UNSET:
            updates["guild_id"] = guild_id
        if channel_id is not UNSET:
            updates["channel_id"] = channel_id
        if creator_id is not UNSET:
            updates["creator_id"] = creator_id
        if category_key is not UNSET:
            updates["category_key"] = category_key
        if status is not UNSET:
            updates["status"] = status.value
        if created_at is not UNSET:
            updates["created_at"] = created_at
        if has_user_message is not UNSET:
            updates["has_user_message"] = to_db_bool(bool(has_user_message))
        if last_user_message_at is not UNSET:
            updates["last_user_message_at"] = last_user_message_at
        if claimed_by is not UNSET:
            updates["claimed_by"] = claimed_by
        if priority is not UNSET:
            updates["priority"] = priority.value
        if priority_before_sleep is not UNSET:
            updates["priority_before_sleep"] = (
                priority_before_sleep.value if priority_before_sleep is not None else None
            )
        if status_before is not UNSET:
            updates["status_before"] = status_before.value if status_before is not None else None
        if transfer_target_category is not UNSET:
            updates["transfer_target_category"] = transfer_target_category
        if transfer_initiated_by is not UNSET:
            updates["transfer_initiated_by"] = transfer_initiated_by
        if transfer_reason is not UNSET:
            updates["transfer_reason"] = transfer_reason
        if transfer_execute_at is not UNSET:
            updates["transfer_execute_at"] = transfer_execute_at
        if transfer_history_json is not UNSET:
            updates["transfer_history_json"] = transfer_history_json
        if staff_panel_message_id is not UNSET:
            updates["staff_panel_message_id"] = staff_panel_message_id

        if updated_at is not UNSET:
            updates["updated_at"] = updated_at
        elif updates:
            updates["updated_at"] = utc_now_iso()

        if not updates:
            return self.get_by_ticket_id(ticket_id, connection=connection)

        set_clause, parameters = build_update_set_clause(updates)
        parameters.append(ticket_id)

        with self.write_connection(connection) as current_connection:
            cursor = current_connection.execute(
                f"UPDATE tickets SET {set_clause} WHERE ticket_id = ?;",
                parameters,
            )
            if cursor.rowcount == 0:
                return None

        return self.get_by_ticket_id(ticket_id, connection=connection)

    def delete(
        self,
        ticket_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        with self.write_connection(connection) as current_connection:
            cursor = current_connection.execute(
                "DELETE FROM tickets WHERE ticket_id = ?;",
                (ticket_id,),
            )
            return cursor.rowcount > 0
