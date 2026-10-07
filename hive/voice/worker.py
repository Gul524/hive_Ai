"""Isolated CPU worker for optional faster-whisper inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    transcribe = commands.add_parser("transcribe")
    transcribe.add_argument("--model", required=True)
    transcribe.add_argument("--audio", type=Path, required=True)
    transcribe.add_argument("--model-dir", type=Path)
    transcribe.add_argument("--language")
    download = commands.add_parser("download-model")
    download.add_argument("--model", required=True)
    download.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    from faster_whisper import WhisperModel

    model = WhisperModel(
        args.model, device="cpu", compute_type="int8",
        download_root=str(args.model_dir) if args.model_dir else None,
        local_files_only=args.command != "download-model",
    )
    if args.command == "download-model":
        print(json.dumps({"model": args.model, "ready": True}))
        return 0
    segments, info = model.transcribe(str(args.audio), language=args.language,
                                      vad_filter=True)
    print(json.dumps({"text": " ".join(segment.text.strip() for segment in segments),
                      "language": info.language}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
