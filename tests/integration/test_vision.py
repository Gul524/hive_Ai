import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from hive.gui.vision import target_confidence


@pytest.mark.skipif(not all(shutil.which(tool) for tool in ("convert", "identify", "compare")),
                    reason="ImageMagick is not installed")
def test_gui_target_confidence_detects_changed_screen(tmp_path: Path) -> None:
    original = tmp_path / "original.png"
    changed = tmp_path / "changed.png"
    subprocess.run(["convert", "-size", "100x100", "xc:white", str(original)], check=True)
    subprocess.run(["convert", str(original), "-fill", "black", "-draw",
                    "rectangle 20,20 80,80", str(changed)], check=True)
    same = asyncio.run(target_confidence(original, original, x=50, y=50))
    different = asyncio.run(target_confidence(original, changed, x=50, y=50))
    assert same == 1.0
    assert different < 0.9
