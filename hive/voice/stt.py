from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import json
import sys
import tempfile
from pathlib import Path


class SpeechToText(ABC):
    @abstractmethod
    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str: ...


class FasterWhisperSTT(SpeechToText):
    def __init__(self, model: str = "base", *, model_dir: Path | None = None,
                 timeout_seconds: int = 180):
        self.model = model
        self.model_dir = model_dir
        self.timeout_seconds = timeout_seconds

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        if not audio.startswith(b"RIFF") or len(audio) > 25_000_000:
            raise ValueError("STT expects a WAV file under 25 MB")
        with tempfile.TemporaryDirectory(prefix="hive-stt-") as folder:
            wav = Path(folder) / "recording.wav"
            wav.write_bytes(audio)
            argv = [sys.executable, "-m", "hive.voice.worker", "transcribe",
                    "--model", self.model, "--audio", str(wav)]
            if self.model_dir:
                argv.extend(["--model-dir", str(self.model_dir)])
            if language:
                argv.extend(["--language", language])
            process = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL, start_new_session=True,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout_seconds)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                process.kill()
                await process.communicate()
                raise
            if process.returncode:
                raise RuntimeError(f"STT failed: {stderr.decode(errors='replace')[:400]}")
            data = json.loads(stdout)
            return str(data["text"]).strip()
