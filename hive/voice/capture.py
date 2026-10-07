"""Push-to-talk recording with PipeWire; always closes the recorder."""

from __future__ import annotations

import asyncio
import signal
import tempfile
from pathlib import Path


async def record_wav(seconds: int = 8) -> bytes:
    if not 1 <= seconds <= 60:
        raise ValueError("Recording duration must be between 1 and 60 seconds")
    with tempfile.TemporaryDirectory(prefix="hive-record-") as folder:
        path = Path(folder) / "speech.wav"
        process = await asyncio.create_subprocess_exec(
            "pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", str(path),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        try:
            await asyncio.sleep(seconds)
            if process.returncode is None:
                process.send_signal(signal.SIGINT)
            _, stderr = await asyncio.wait_for(process.communicate(), 5)
            if not path.exists() or path.stat().st_size < 44:
                raise RuntimeError(f"Audio recording failed: {stderr.decode(errors='replace')[:400]}")
            return path.read_bytes()
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()
