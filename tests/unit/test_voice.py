import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from hive.config.schema import HiveConfig
from hive.core.approvals import ApprovalError, ApprovalStore
from hive.core.models import ApprovalRequest, LanguageCode, RiskLevel, utc_now
from hive.voice.approval import approve_from_voice
from hive.voice.pipeline import VoicePipeline
from hive.voice.stt import SpeechToText
from hive.voice.transliterate import roman_to_urdu, urdu_to_roman
from hive.voice.tts import TextToSpeech


class FakeSTT(SpeechToText):
    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        return "مجھے nginx انسٹال کرو"


class FakeTTS(TextToSpeech):
    async def synthesize(self, text: str, *, language: str) -> bytes:
        assert language == "ur_roman"
        return b"RIFFfake"


def test_voice_language_pipeline(tmp_path: Path) -> None:
    config = HiveConfig.model_validate({"voice": {"enabled": True}})
    pipeline = VoicePipeline(config, stt=FakeSTT(), tts=FakeTTS())
    text, language = asyncio.run(pipeline.transcribe(b"RIFFfake"))
    assert text == "mujhe nginx install karo"
    assert language == LanguageCode.UR_ROMAN
    output = asyncio.run(pipeline.speak(text, language=language, output=tmp_path / "speech.wav"))
    assert output.read_bytes() == b"RIFFfake"
    with pytest.raises(FileExistsError):
        asyncio.run(pipeline.speak(text, language=language, output=output))


def test_voice_approval_requires_two_explicit_matches(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "hive.db")
    request = store.add(ApprovalRequest(
        task_id="t", agent_id="a", plan_id="p", title="Install", description="Install package",
        risk_level=RiskLevel.HIGH, verification_summary="rpm -q",
        rollback_summary="review", plan_digest="digest",
        expires_at=utc_now() + timedelta(minutes=10),
    ))
    with pytest.raises(ApprovalError):
        approve_from_voice(store, request.approval_id, f"approve {request.approval_id}", "yes")
    with pytest.raises(ApprovalError):
        approve_from_voice(store, request.approval_id, "approve something else", request.approval_id)
    approved = approve_from_voice(store, request.approval_id,
                                  f"approve {request.approval_id}", request.approval_id)
    assert approved.status.value == "approved"


def test_urdu_transliteration() -> None:
    assert urdu_to_roman("مجھے nginx انسٹال کرو") == "mujhe nginx install karo"
    assert roman_to_urdu("mujhe nginx install karo") == "مجھے nginx انسٹال کرو"
