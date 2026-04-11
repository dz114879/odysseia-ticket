from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from collections.abc import Iterator, Sequence


class DatabaseManager:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        self.initialize()
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute("PRAGMA journal_mode = WAL;")
        connection.execute("PRAGMA synchronous = NORMAL;")
        connection.execute("PRAGMA busy_timeout = 30000;")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN;")
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    transaction = session

    def execute(self, query: str, params: Sequence[Any] = ()) -> int | None:
        with self.session() as connection:
            cursor = connection.execute(query, params)
            return cursor.lastrowid

    def executemany(self, query: str, param_sets: Sequence[Sequence[Any]]) -> None:
        with self.session() as connection:
            connection.executemany(query, param_sets)

    def execute_script(self, script: str) -> None:
        with self.session() as connection:
            connection.executescript(script)

    def fetchone(self, query: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        connection = self.connect()
        try:
            return connection.execute(query, params).fetchone()
        finally:
            connection.close()

    def fetchall(self, query: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        connection = self.connect()
        try:
            return list(connection.execute(query, params).fetchall())
        finally:
            connection.close()
