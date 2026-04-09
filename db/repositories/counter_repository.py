from __future__ import annotations

import sqlite3

from core.models import TicketCounterRecord
from db.repositories.base import BaseRepository


class CounterRepository(BaseRepository):
    def _row_to_record(self, row: sqlite3.Row) -> TicketCounterRecord:
        return TicketCounterRecord(
            guild_id=row["guild_id"],
            category_key=row["category_key"],
            next_number=row["next_number"],
        )

    def get_counter(
        self,
        guild_id: int,
        category_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> TicketCounterRecord | None:
        with self.read_connection(connection) as current_connection:
            row = current_connection.execute(
                """
                SELECT *
                FROM ticket_counters
                WHERE guild_id = ? AND category_key = ?;
                """,
                (guild_id, category_key),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def upsert_counter(
        self,
        record: TicketCounterRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> TicketCounterRecord:
        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                """
                INSERT INTO ticket_counters (guild_id, category_key, next_number)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, category_key) DO UPDATE SET
                    next_number = excluded.next_number;
                """,
                (record.guild_id, record.category_key, record.next_number),
            )
        return self.get_counter(record.guild_id, record.category_key, connection=connection) or record

    def increment(
        self,
        guild_id: int,
        category_key: str,
        *,
        step: int = 1,
        connection: sqlite3.Connection | None = None,
    ) -> TicketCounterRecord:
        if step <= 0:
            raise ValueError("step 必须大于 0。")

        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                """
                INSERT INTO ticket_counters (guild_id, category_key, next_number)
                VALUES (?, ?, 1)
                ON CONFLICT(guild_id, category_key) DO NOTHING;
                """,
                (guild_id, category_key),
            )
            current_connection.execute(
                """
                UPDATE ticket_counters
                SET next_number = next_number + ?
                WHERE guild_id = ? AND category_key = ?;
                """,
                (step, guild_id, category_key),
            )
            row = current_connection.execute(
                """
                SELECT *
                FROM ticket_counters
                WHERE guild_id = ? AND category_key = ?;
                """,
                (guild_id, category_key),
            ).fetchone()

        if row is None:
            raise RuntimeError("计数器更新后未能读取记录。")

        return self._row_to_record(row)

    def delete_counter(
        self,
        guild_id: int,
        category_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        with self.write_connection(connection) as current_connection:
            cursor = current_connection.execute(
                "DELETE FROM ticket_counters WHERE guild_id = ? AND category_key = ?;",
                (guild_id, category_key),
            )
            return cursor.rowcount > 0
