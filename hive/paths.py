"""Resolve Hive's user-scoped paths without writing to them."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


class HivePaths(BaseModel):
    config_dir: Path
    data_dir: Path
    state_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.yaml"

    @property
    def database_file(self) -> Path:
        return self.data_dir / "hive.db"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"


def resolve_paths(*, environ: dict[str, str] | None = None, home: Path | None = None) -> HivePaths:
    """Use explicit Hive overrides, then XDG locations, then XDG defaults."""
    env = os.environ if environ is None else environ
    base = Path.home() if home is None else home

    def location(hive_key: str, xdg_key: str, xdg_default: str) -> Path:
        explicit = env.get(hive_key)
        if explicit:
            return Path(explicit).expanduser().resolve()
        xdg = env.get(xdg_key)
        parent = Path(xdg).expanduser() if xdg else base / xdg_default
        return (parent / "hive").resolve()

    return HivePaths(
        config_dir=location("HIVE_CONFIG_DIR", "XDG_CONFIG_HOME", ".config"),
        data_dir=location("HIVE_DATA_DIR", "XDG_DATA_HOME", ".local/share"),
        state_dir=location("HIVE_STATE_DIR", "XDG_STATE_HOME", ".local/state"),
    )
