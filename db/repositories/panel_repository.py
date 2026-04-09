from __future__ import annotations

import sqlite3

from core.models import PanelRecord
from db.repositories.base import (
    UNSET,
    BaseRepository,
    build_update_set_clause,
    from_db_bool,
    to_db_bool,
    utc_now_iso,
)


class PanelRepository(BaseRepository):
    def _row_to_record(self, row: sqlite3.Row) -> PanelRecord:
        return PanelRecord(
            panel_id=row["panel_id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            nonce=row["nonce"],
            is_active=from_db_bool(row["is_active"]),
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create(
        self,
        record: PanelRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> PanelRecord:
        created_at = record.created_at or utc_now_iso()
        updated_at = record.updated_at or created_at

        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                """
                INSERT INTO panels (
                    panel_id,
                    guild_id,
                    channel_id,
                    message_id,
                    nonce,
                    is_active,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.panel_id,
                    record.guild_id,
                    record.channel_id,
                    record.message_id,
                    record.nonce,
                    to_db_bool(record.is_active),
                    record.created_by,
                    created_at,
                    updated_at,
                ),
            )
        return PanelRecord(
            panel_id=record.panel_id,
            guild_id=record.guild_id,
            channel_id=record.channel_id,
            message_id=record.message_id,
            nonce=record.nonce,
            is_active=record.is_active,
            created_by=record.created_by,
            created_at=created_at,
            updated_at=updated_at,
        )

    def upsert(
        self,
        record: PanelRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> PanelRecord:
        created_at = record.created_at or utc_now_iso()
        updated_at = record.updated_at or created_at

        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                """
                INSERT INTO panels (
                    panel_id,
                    guild_id,
                    channel_id,
                    message_id,
                    nonce,
                    is_active,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(panel_id) DO UPDATE SET
                    guild_id = excluded.guild_id,
                    channel_id = excluded.channel_id,
                    message_id = excluded.message_id,
                    nonce = excluded.nonce,
                    is_active = excluded.is_active,
                    created_by = excluded.created_by,
                    updated_at = excluded.updated_at;
                """,
                (
                    record.panel_id,
                    record.guild_id,
                    record.channel_id,
                    record.message_id,
                    record.nonce,
                    to_db_bool(record.is_active),
                    record.created_by,
                    created_at,
                    updated_at,
                ),
            )
        return self.get_by_panel_id(record.panel_id, connection=connection) or PanelRecord(
            panel_id=record.panel_id,
            guild_id=record.guild_id,
            channel_id=record.channel_id,
            message_id=record.message_id,
            nonce=record.nonce,
            is_active=record.is_active,
            created_by=record.created_by,
            created_at=created_at,
            updated_at=updated_at,
        )

    def replace_active_panel(
        self,
        record: PanelRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> PanelRecord:
        if not record.is_active:
            return self.upsert(record, connection=connection)

        with self.write_connection(connection) as current_connection:
            current_connection.execute(
                """
                UPDATE panels
                SET is_active = 0, updated_at = ?
                WHERE guild_id = ? AND panel_id != ? AND is_active = 1;
                """,
                (utc_now_iso(), record.guild_id, record.panel_id),
            )
            return self.upsert(record, connection=current_connection)

    def get_by_panel_id(
        self,
        panel_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> PanelRecord | None:
        with self.read_connection(connection) as current_connection:
            row = current_connection.execute(
                "SELECT * FROM panels WHERE panel_id = ?;",
                (panel_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def get_by_message_id(
        self,
        message_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> PanelRecord | None:
        with self.read_connection(connection) as current_connection:
            row = current_connection.execute(
                "SELECT * FROM panels WHERE message_id = ?;",
                (message_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def get_active_panel(
        self,
        guild_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> PanelRecord | None:
        with self.read_connection(connection) as current_connection:
            row = current_connection.execute(
                """
                SELECT *
                FROM panels
                WHERE guild_id = ? AND is_active = 1
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1;
                """,
                (guild_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_by_guild(
        self,
        guild_id: int,
        *,
        active_only: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> list[PanelRecord]:
        query = "SELECT * FROM panels WHERE guild_id = ?"
        parameters: list[object] = [guild_id]
        if active_only:
            query += " AND is_active = 1"
        query += " ORDER BY created_at DESC, panel_id DESC;"

        with self.read_connection(connection) as current_connection:
            rows = current_connection.execute(query, parameters).fetchall()
        return [self._row_to_record(row) for row in rows]

    def deactivate_guild_panels(
        self,
        guild_id: int,
        *,
        except_panel_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        clauses = ["guild_id = ?", "is_active = 1"]
        parameters: list[object] = [guild_id]
        if except_panel_id is not None:
            clauses.append("panel_id != ?")
            parameters.append(except_panel_id)

        query = (
            "UPDATE panels "
            "SET is_active = 0, updated_at = ? "
            f"WHERE {' AND '.join(clauses)};"
        )
        parameters.insert(0, utc_now_iso())

        with self.write_connection(connection) as current_connection:
            cursor = current_connection.execute(query, parameters)
            return cursor.rowcount

    def update(
        self,
        panel_id: str,
        *,
        guild_id: int | object = UNSET,
        channel_id: int | object = UNSET,
        message_id: int | object = UNSET,
        nonce: str | object = UNSET,
        is_active: bool | object = UNSET,
        created_by: int | object = UNSET,
        created_at: str | object = UNSET,
        updated_at: str | object = UNSET,
        connection: sqlite3.Connection | None = None,
    ) -> PanelRecord | None:
        updates: dict[str, object] = {}

        if guild_id is not UNSET:
            updates["guild_id"] = guild_id
        if channel_id is not UNSET:
            updates["channel_id"] = channel_id
        if message_id is not UNSET:
            updates["message_id"] = message_id
        if nonce is not UNSET:
            updates["nonce"] = nonce
        if is_active is not UNSET:
            updates["is_active"] = to_db_bool(bool(is_active))
        if created_by is not UNSET:
            updates["created_by"] = created_by
        if created_at is not UNSET:
            updates["created_at"] = created_at

        if updated_at is not UNSET:
            updates["updated_at"] = updated_at
        elif updates:
            updates["updated_at"] = utc_now_iso()

        if not updates:
            return self.get_by_panel_id(panel_id, connection=connection)

        set_clause, parameters = build_update_set_clause(updates)
        parameters.append(panel_id)

        with self.write_connection(connection) as current_connection:
            cursor = current_connection.execute(
                f"UPDATE panels SET {set_clause} WHERE panel_id = ?;",
                parameters,
            )
            if cursor.rowcount == 0:
                return None

        return self.get_by_panel_id(panel_id, connection=connection)

    def delete(
        self,
        panel_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        with self.write_connection(connection) as current_connection:
            cursor = current_connection.execute(
                "DELETE FROM panels WHERE panel_id = ?;",
                (panel_id,),
            )
            return cursor.rowcount > 0
