import json
import sqlite3
from pathlib import Path

import pytest

from hive.cli.app import main
from hive.config import load_config
from hive.core.errors import ConfigurationError
from hive.observability.logger import configure_logger
from hive.paths import resolve_paths


def test_config_example_loads() -> None:
    config = load_config(Path("config.example.yaml"))
    assert config.core.local_first
    assert not config.models.cloud_fallback.enabled
    assert config.retry.max_retries == 2


def test_invalid_config_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("retry:\n  max_retries: 3\n")
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_paths_follow_xdg(tmp_path: Path) -> None:
    paths = resolve_paths(environ={"XDG_DATA_HOME": str(tmp_path)}, home=tmp_path)
    assert paths.data_dir == tmp_path / "hive"
    assert paths.config_file == tmp_path / ".config/hive/config.yaml"


def test_init_creates_wal_and_redacted_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HIVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HIVE_STATE_DIR", str(tmp_path / "state"))
    assert main(["init"]) == 0
    database = tmp_path / "data/hive.db"
    assert database.exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    logger = configure_logger(tmp_path / "state/logs")
    logger.info("authorization: Bearer abc123", extra={"details": {"api_key": "secret"}})
    log_records = [json.loads(line) for line in (tmp_path / "state/logs/hive.jsonl").read_text().splitlines()]
    assert "abc123" not in json.dumps(log_records)
    assert "secret" not in json.dumps(log_records)
    assert log_records[-1]["details"]["api_key"] == "[REDACTED]"
