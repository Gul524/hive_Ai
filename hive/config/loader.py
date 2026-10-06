"""Load a user config file without interpreting arbitrary YAML objects."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from hive.config.schema import HiveConfig
from hive.core.errors import ConfigurationError
from hive.paths import HivePaths, resolve_paths


def load_config(path: Path | None = None, *, paths: HivePaths | None = None) -> HiveConfig:
    config_path = path if path is not None else (paths or resolve_paths()).config_file
    if not config_path.exists():
        return HiveConfig()
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            contents = yaml.safe_load(stream)
        if contents is None:
            contents = {}
        if not isinstance(contents, dict):
            raise ConfigurationError(f"Config must be a YAML mapping: {config_path}")
        return HiveConfig.model_validate(contents)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"Cannot load config {config_path}: {exc}") from exc
