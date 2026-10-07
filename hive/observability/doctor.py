"""Read-only diagnostics for local Hive dependencies and state."""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

import httpx
from rich.console import Console
from rich.table import Table

from hive.config.schema import HiveConfig
from hive.paths import HivePaths


def diagnostics(config: HiveConfig, paths: HivePaths) -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []
    checks.append(("Python", "OK" if sys.version_info >= (3, 11) else "FAIL", sys.version.split()[0]))
    for module in ("pydantic", "rich", "yaml", "httpx"):
        checks.append((module, "OK" if importlib.util.find_spec(module) else "FAIL", "installed" if importlib.util.find_spec(module) else "missing"))
    checks.append(("Config", "OK", str(paths.config_file) if paths.config_file.exists() else "defaults"))
    checks.append(("Data directory", "OK" if paths.data_dir.exists() else "WARN", str(paths.data_dir)))
    checks.append(("Log directory", "OK" if paths.logs_dir.exists() else "WARN", str(paths.logs_dir)))
    if paths.database_file.exists():
        try:
            with sqlite3.connect(paths.database_file) as connection:
                mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                pending = (connection.execute("SELECT count(*) FROM approvals WHERE status='pending'").fetchone()[0]
                           if "approvals" in tables else 0)
                open_resources = (connection.execute("SELECT count(*) FROM resources WHERE state!='released'").fetchone()[0]
                         if "resources" in tables else 0)
            checks.append(("SQLite", "OK" if mode == "wal" else "WARN", mode))
            checks.append(("Approvals", "OK", f"{pending} pending"))
            checks.append(("Open resources", "OK", str(open_resources)))
        except sqlite3.Error as exc:
            checks.append(("SQLite", "FAIL", str(exc)))
    else:
        checks.append(("SQLite", "WARN", "Run hive init"))
    for binary in ("ollama", "dnf", "rpm", "systemctl"):
        checks.append((binary, "OK" if shutil.which(binary) else "WARN", shutil.which(binary) or "not found"))
    if config.browser.enabled:
        checks.append(("Playwright", "OK" if importlib.util.find_spec("playwright") else "WARN",
                       "installed" if importlib.util.find_spec("playwright") else "install .[browser]"))
    if config.voice.enabled:
        for module in ("faster_whisper", "piper"):
            checks.append((module, "OK" if importlib.util.find_spec(module) else "WARN",
                           "installed" if importlib.util.find_spec(module) else "install .[voice]"))
        for binary in ("pw-record", "ffplay"):
            checks.append((binary, "OK" if shutil.which(binary) else "WARN", shutil.which(binary) or "not found"))
    if config.gui.enabled:
        for binary in ("Xvfb", "xdotool", "firefox", "import", "compare"):
            checks.append((binary, "OK" if shutil.which(binary) else "WARN", shutil.which(binary) or "not found"))
    try:
        response = httpx.get(f"{str(config.models.local.base_url).rstrip('/')}/api/tags", timeout=2)
        response.raise_for_status()
        available = {item.get("name") for item in response.json().get("models", [])}
        checks.append(("Ollama API", "OK", "reachable"))
        checks.append(("Local model", "OK" if config.models.local.model in available else "WARN",
                       config.models.local.model))
    except (httpx.HTTPError, ValueError, KeyError):
        checks.append(("Ollama API", "WARN", "unreachable"))
    free_bytes = shutil.disk_usage(paths.data_dir if paths.data_dir.exists() else Path.home()).free
    checks.append(("Disk free", "OK" if free_bytes > 100_000_000 else "WARN", f"{free_bytes // 1_000_000} MB"))
    return checks


def render_doctor(console: Console, config: HiveConfig, paths: HivePaths) -> None:
    table = Table(title="Hive doctor")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail")
    for row in diagnostics(config, paths):
        table.add_row(*row)
    console.print(table)
