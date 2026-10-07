"""Compare an approved target region to the live screenshot."""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path


async def _tool(*argv: str, timeout: float = 10) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.communicate()
        raise
    return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def image_size(path: Path) -> tuple[int, int]:
    code, stdout, stderr = await _tool("identify", "-format", "%w %h", str(path))
    if code:
        raise ValueError(f"Cannot inspect screenshot: {stderr[:200]}")
    width, height = map(int, stdout.split())
    return width, height


async def target_confidence(reference: Path, current: Path, *, x: int, y: int,
                            radius: int = 48) -> float:
    if not reference.is_file() or not current.is_file():
        raise FileNotFoundError("Both screenshots are required")
    width, height = await image_size(reference)
    current_size = await image_size(current)
    if current_size != (width, height) or not 0 <= x < width or not 0 <= y < height:
        return 0.0
    left = max(0, x - radius)
    top = max(0, y - radius)
    crop_width = min(width - left, radius * 2)
    crop_height = min(height - top, radius * 2)
    geometry = f"{crop_width}x{crop_height}+{left}+{top}"
    with tempfile.TemporaryDirectory(prefix="hive-vision-") as folder:
        first = Path(folder) / "reference.png"
        second = Path(folder) / "current.png"
        for source, destination in ((reference, first), (current, second)):
            code, _, stderr = await _tool(
                "convert", str(source), "-crop", geometry, "+repage", str(destination))
            if code:
                raise ValueError(f"Cannot crop screenshot: {stderr[:200]}")
        code, _, stderr = await _tool("compare", "-metric", "MAE", str(first), str(second), "null:")
        if code not in (0, 1):
            raise ValueError(f"Cannot compare screenshots: {stderr[:200]}")
        match = re.search(r"\(([0-9.]+)\)", stderr)
        if not match:
            raise ValueError("Screenshot comparison returned no score")
        return max(0.0, min(1.0, 1.0 - float(match.group(1))))
