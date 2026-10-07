"""Local, aggregate operational metrics. No network export."""

from __future__ import annotations

from pathlib import Path

from hive.core.state import open_database


class MetricsStore:
    def __init__(self, database_file: Path):
        self.database_file = database_file
        with open_database(database_file) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metrics (name TEXT PRIMARY KEY, count INTEGER NOT NULL, "
                "total REAL NOT NULL)"
            )

    def add(self, name: str, value: float = 1.0) -> None:
        if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz_" for ch in name):
            raise ValueError("Invalid metric name")
        with open_database(self.database_file) as connection:
            connection.execute(
                "INSERT INTO metrics (name, count, total) VALUES (?, 1, ?) "
                "ON CONFLICT(name) DO UPDATE SET count=count+1, total=total+excluded.total",
                (name, value),
            )

    def list(self) -> list[dict]:
        with open_database(self.database_file) as connection:
            return [dict(zip(("name", "count", "total"), row))
                    for row in connection.execute("SELECT name, count, total FROM metrics ORDER BY name")]
