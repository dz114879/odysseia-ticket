from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from db.connection import DatabaseManager

UNSET: Any = object()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_db_bool(value: bool) -> int:
    return 1 if value else 0


def from_db_bool(value: Any) -> bool:
    if value is None:
        return False
    return bool(int(value))


def build_update_set_clause(updates: dict[str, Any]) -> tuple[str, list[Any]]:
    if not updates:
        raise ValueError("updates 不能为空。")

    assignments = [f"{column} = ?" for column in updates]
    parameters = list(updates.values())
    return ", ".join(assignments), parameters


class BaseRepository:
    def __init__(self, database: DatabaseManager):
        self.database = database

    @contextmanager
    def read_connection(
        self,
        connection: sqlite3.Connection | None = None,
    ) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            yield connection
            return

        managed_connection = self.database.connect()
        try:
            yield managed_connection
        finally:
            managed_connection.close()

    @contextmanager
    def write_connection(
        self,
        connection: sqlite3.Connection | None = None,
    ) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            yield connection
            return

        with self.database.session() as managed_connection:
            yield managed_connection
