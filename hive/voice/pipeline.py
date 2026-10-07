"""Speech input, language mirroring, and optional spoken output."""

from __future__ import annotations

from pathlib import Path

from hive.config.schema import HiveConfig
from hive.core.models import LanguageCode
from hive.voice.capture import record_wav
from hive.voice.language import detect_language
from hive.voice.stt import FasterWhisperSTT, SpeechToText
from hive.voice.transliterate import roman_to_urdu, urdu_to_roman
from hive.voice.tts import PiperTTS, TextToSpeech


class VoicePipeline:
    def __init__(self, config: HiveConfig, *, stt: SpeechToText | None = None,
                 tts: TextToSpeech | None = None):
        self.config = config
        self.stt = stt or FasterWhisperSTT(
            config.voice.stt_model, model_dir=config.voice.stt_model_dir,
            timeout_seconds=config.timeouts.llm_request_seconds + 60,
        )
        self.tts = tts or PiperTTS(english_voice=config.voice.tts_voice_en,
                                   urdu_voice=config.voice.tts_voice_ur)

    async def transcribe(self, audio: bytes) -> tuple[str, LanguageCode]:
        if not self.config.voice.enabled:
            raise RuntimeError("Voice is disabled in config")
        language_hint = None if self.config.voice.language_mode == "auto" else self.config.voice.language_mode
        raw = await self.stt.transcribe(audio, language=language_hint)
        language = detect_language(raw)
        text = urdu_to_roman(raw) if language == LanguageCode.UR_ROMAN else raw
        return text, language

    async def listen(self, *, seconds: int | None = None) -> tuple[str, LanguageCode]:
        audio = await record_wav(seconds or self.config.voice.record_seconds)
        return await self.transcribe(audio)

    async def speak(self, text: str, *, language: LanguageCode, output: Path) -> Path:
        if not self.config.voice.enabled:
            raise RuntimeError("Voice is disabled in config")
        spoken_text = roman_to_urdu(text) if language == LanguageCode.UR_ROMAN else text
        wav = await self.tts.synthesize(spoken_text, language=language.value)
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with output.open("xb") as stream:
            stream.write(wav)
        output.chmod(0o600)
        return output
