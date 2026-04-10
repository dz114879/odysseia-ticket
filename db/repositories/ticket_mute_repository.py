from __future__ import annotations

import sqlite3

from core.models import TicketMuteRecord
from db.repositories.base import BaseRepository, build_update_set_clause, utc_now_iso


class TicketMuteRepository(BaseRepository):
    def _row_to_record(self, row: sqlite3.Row) -> TicketMuteRecord:
        return TicketMuteRecord(
            ticket_id=row["ticket_id"],
            user_id=row["user_id"],
            muted_by=row["muted_by"],
            reason=row["reason"],
            expire_at=row["expire_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert(
        self,
        record: TicketMuteRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> TicketMuteRecord:
        created_at = record.created_at or utc_now_iso()
        updated_at = record.updated_at or created_at

        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                """
                INSERT INTO ticket_mutes (
                    ticket_id,
                    user_id,
                    muted_by,
                    reason,
                    expire_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticket_id, user_id) DO UPDATE SET
                    muted_by = excluded.muted_by,
                    reason = excluded.reason,
                    expire_at = excluded.expire_at,
                    updated_at = excluded.updated_at;
                """,
                (
                    record.ticket_id,
                    record.user_id,
                    record.muted_by,
                    record.reason,
                    record.expire_at,
                    created_at,
                    updated_at,
                ),
            )
        return self.get_by_ticket_and_user(record.ticket_id, record.user_id, connection=connection) or TicketMuteRecord(
            ticket_id=record.ticket_id,
            user_id=record.user_id,
            muted_by=record.muted_by,
            reason=record.reason,
            expire_at=record.expire_at,
            created_at=created_at,
            updated_at=updated_at,
        )

    def get_by_ticket_and_user(
        self,
        ticket_id: str,
        user_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> TicketMuteRecord | None:
        with self.read_connection(connection) as current_connection:
            row = current_connection.execute(
                "SELECT * FROM ticket_mutes WHERE ticket_id = ? AND user_id = ?;",
                (ticket_id, user_id),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_by_ticket(
        self,
        ticket_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[TicketMuteRecord]:
        with self.read_connection(connection) as current_connection:
            rows = current_connection.execute(
                "SELECT * FROM ticket_mutes WHERE ticket_id = ? ORDER BY created_at ASC, user_id ASC;",
                (ticket_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_due_expirations(
        self,
        expire_before: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[TicketMuteRecord]:
        with self.read_connection(connection) as current_connection:
            rows = current_connection.execute(
                """
                SELECT * FROM ticket_mutes
                WHERE expire_at IS NOT NULL
                AND expire_at <= ?
                ORDER BY expire_at ASC, created_at ASC;
                """,
                (expire_before,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def touch(
        self,
        ticket_id: str,
        user_id: int,
        *,
        muted_by: int,
        reason: str | None,
        expire_at: str | None,
        updated_at: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> TicketMuteRecord | None:
        updates = {
            "muted_by": muted_by,
            "reason": reason,
            "expire_at": expire_at,
            "updated_at": updated_at or utc_now_iso(),
        }
        set_clause, parameters = build_update_set_clause(updates)
        parameters.extend((ticket_id, user_id))

        with self.write_connection(connection) as current_connection:
            cursor = current_connection.execute(
                f"UPDATE ticket_mutes SET {set_clause} WHERE ticket_id = ? AND user_id = ?;",
                parameters,
            )
            if cursor.rowcount == 0:
                return None
        return self.get_by_ticket_and_user(ticket_id, user_id, connection=connection)

    def delete(
        self,
        ticket_id: str,
        user_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        with self.write_connection(connection) as current_connection:
            cursor = current_connection.execute(
                "DELETE FROM ticket_mutes WHERE ticket_id = ? AND user_id = ?;",
                (ticket_id, user_id),
            )
            return cursor.rowcount > 0
