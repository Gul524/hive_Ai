from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from pathlib import Path

from hive.core.models import LanguageCode


class TextToSpeech(ABC):
    @abstractmethod
    async def synthesize(self, text: str, *, language: str) -> bytes: ...


class PiperTTS(TextToSpeech):
    def __init__(self, *, english_voice: Path | None, urdu_voice: Path | None,
                 binary: str = "piper", timeout_seconds: int = 60):
        self.english_voice = english_voice
        self.urdu_voice = urdu_voice
        self.binary = binary
        self.timeout_seconds = timeout_seconds

    async def synthesize(self, text: str, *, language: str) -> bytes:
        voice = self.urdu_voice if language == LanguageCode.UR_ROMAN else self.english_voice
        if voice is None or not voice.is_file():
            raise FileNotFoundError(f"Piper voice model is not configured for {language}")
        if not text.strip() or len(text) > 5000:
            raise ValueError("TTS text must contain 1 to 5000 characters")
        process = await asyncio.create_subprocess_exec(
            self.binary, "--model", str(voice.resolve()), "--output_file", "-",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(text.encode("utf-8")), self.timeout_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.communicate()
            raise
        if process.returncode or not stdout.startswith(b"RIFF"):
            raise RuntimeError(f"Piper synthesis failed: {stderr.decode(errors='replace')[:400]}")
        return stdout
