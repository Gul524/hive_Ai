"""Read-only unified diffs for plan review."""

from __future__ import annotations

import difflib
from pathlib import Path


def unified_diff(before: str, after: str, *, path: str = "file") -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def file_diff(path: Path, after: str) -> str:
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    return unified_diff(before, after, path=str(path))
