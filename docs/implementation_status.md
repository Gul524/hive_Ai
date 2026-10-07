# Implementation status

The master plan is implemented as an approval-based CLI with testable adapters. This file distinguishes code coverage from integrations that need local runtimes or live system access.

| Plan area | Implemented | Verification |
| --- | --- | --- |
| Foundation, config, Rich UI, state, approvals | SQLite WAL, task checkpoints, digests, expiry, queue, history | Automated tests and CLI smoke check |
| Model hub and guided workflow | Ollama, optional cloud fallback, cache, structured repair, clarification, research, options, discussion, allowlisted Fedora proposal | Fake model integration tests; live model unavailable here |
| Policy, execution, OS adapter | Fedora package/service/firewall templates, file writes, verification, retries for transient safe commands, PTY support, durable backup and approved restore | Automated tests; no system-changing command run |
| Agents and resources | Registry, scheduler, locks with heartbeat, grid, resource manager, failed task records, watchdog | Automated tests; no long-running multi-process load test |
| Research | Local man/RPM, explicit HTTPS pages, optional Brave Search, citations and untrusted-content boundary | Unit and mocked HTTP tests; no live search key |
| Voice | PipeWire capture, faster-whisper STT, Roman Urdu handling, Piper TTS, typed confirmation for spoken approvals | Local English and Urdu WAV synthesis verified; English sample transcribed with downloaded Whisper base model; live microphone and speaker playback await an interactive check |
| Browser | Isolated Playwright context, page actions, uploads/downloads, payment and private URL checks, screenshots, cleanup | Fake browser integration tests; Playwright Chromium absent |
| GUI | Per-agent Xvfb sessions, isolated Firefox launch, approved X11 input, screenshot confidence, cleanup | Fake screen/input tests and real ImageMagick comparison; Xvfb and xdotool absent |
| Observability and hardening | Doctor, audit, local metrics, trace, redaction, recovery | Automated tests and CLI smoke check |

The following need runtime setup or additional platform work before they can be claimed as live integrations:

1. Install and configure Ollama plus the selected local model, then run a real guided proposal.
2. Run an interactive microphone and speaker check for voice. Install the browser Python extra and Playwright Chromium, then run live browser checks.
3. Install Xvfb and xdotool, then run a live virtual GUI session. Host Wayland and accessibility input remain extension interfaces as specified in Phase 15.
4. Run a Fedora package/service plan on a disposable test machine with approvals. Development tests deliberately avoid system changes.

The guided operator currently maps only the allowlisted Fedora actions to executable plans. File changes, browser actions, and GUI actions use their dedicated deterministic plan commands. Other natural-language requests require a manual choice of those commands.
