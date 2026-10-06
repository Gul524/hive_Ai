"""SQLite bootstrap. Task tables are introduced in Phase 2."""

from __future__ import annotations

import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from hive.core.errors import StateError


@contextmanager
def open_database(database_file: Path) -> Iterator[sqlite3.Connection]:
    try:
        database_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(database_file, timeout=10)
        os.chmod(database_file, 0o600)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_version (version) "
                "SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM schema_version)"
            )
            connection.commit()
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise StateError(f"Cannot initialize database {database_file}: {exc}") from exc
