"""CLI voice flows with explicit approval confirmation."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from rich.console import Console

from hive.cli.chat import chat_once
from hive.cli.render import approval_panel
from hive.config.schema import HiveConfig
from hive.core.approvals import ApprovalStore
from hive.core.models import LanguageCode, new_id
from hive.paths import HivePaths
from hive.voice.approval import approve_from_voice
from hive.voice.language import detect_language
from hive.voice.pipeline import VoicePipeline


async def play_wav(path: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        "pw-play", str(path), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), 120)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(f"Audio playback failed: {stderr.decode(errors='replace')[:300]}")


async def listen_command(console: Console, config: HiveConfig, paths: HivePaths,
                         *, seconds: int | None = None, approval_id: str | None = None,
                         talk: bool = False) -> None:
    pipeline = VoicePipeline(config)
    console.print("Recording now...")
    transcript, language = await pipeline.listen(seconds=seconds)
    console.print(f"Heard: {transcript}")
    if approval_id:
        store = ApprovalStore(paths.database_file, audit_file=paths.state_dir / "audit.jsonl")
        approval_panel(console, store.get(approval_id))
        typed = console.input("Type the full approval ID to confirm: ")
        approval_panel(console, approve_from_voice(store, approval_id, transcript, typed))
        return
    if talk:
        answer = await chat_once(transcript, config, database_file=paths.database_file)
        console.print(answer)
        output = paths.state_dir / "voice" / f"{new_id()}.wav"
        await pipeline.speak(answer, language=language, output=output)
        await play_wav(output)


async def speak_command(console: Console, config: HiveConfig, text: str, output: Path,
                        *, play: bool = False) -> None:
    pipeline = VoicePipeline(config)
    language = detect_language(text)
    result = await pipeline.speak(text, language=language, output=output)
    console.print(f"Saved speech: {result}")
    if play:
        await play_wav(result)


async def download_stt_model(config: HiveConfig) -> None:
    argv = [sys.executable, "-m", "hive.voice.worker", "download-model",
            "--model", config.voice.stt_model]
    if config.voice.stt_model_dir:
        argv.extend(["--model-dir", str(config.voice.stt_model_dir)])
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), 900)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(f"STT model download failed: {stderr.decode(errors='replace')[:400]}")
