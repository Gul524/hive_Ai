# Hive AI

Hive AI is a local-first Linux operator with persistent tasks, reviewable plans, explicit approvals, and constrained execution. Fedora is the primary supported distribution. The [master implementation plan](hive_ai_master_implementation_plan.md) is the product specification.

## Install

Python 3.11 or newer is required. Core features use the base install; browser and voice runtimes are optional.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/hive init
.venv/bin/hive doctor
.venv/bin/python -m pytest
```

Copy [config.example.yaml](config.example.yaml) to `~/.config/hive/config.yaml` to enable browser, voice, or GUI. Default data and state directories are `~/.local/share/hive` and `~/.local/state/hive`. `HIVE_CONFIG_DIR`, `HIVE_DATA_DIR`, and `HIVE_STATE_DIR` override those locations.

Optional local runtimes:

```bash
.venv/bin/python -m pip install -e '.[browser,voice]'
.venv/bin/hive browser install-chromium
.venv/bin/hive voice download-stt-model
.venv/bin/hive voice download-tts-voices
```

Voice recording uses `pw-record`; playback uses `pw-play`. Set `voice.tts_voice_en` and optionally `voice.tts_voice_ur` to local Piper voice model paths. GUI sessions use `Xvfb`, `xdotool`, ImageMagick (`import`, `identify`, `convert`, `compare`), and Firefox. `hive doctor` reports missing tools when the corresponding module is enabled. No model or system package is installed automatically by `hive init`.

## Tasks, plans, and approvals

Create a task, then create a plan. Plan output includes the plan ID and approval IDs. Review each approval with `hive approval show ID` before approving it. High-risk actions need their own step approval as well as the plan approval. Plans are single-use, bound to a digest, and expire with their approvals.

```bash
.venv/bin/hive task create "Create a note"
.venv/bin/hive plan write-file TASK_ID "$PWD/note.txt" --workspace "$PWD" --content "hello"
.venv/bin/hive approval pending
.venv/bin/hive approval show APPROVAL_ID
.venv/bin/hive approval approve APPROVAL_ID
.venv/bin/hive run PLAN_ID
.venv/bin/hive trace TASK_ID
```

File writes stay in the approved workspace, check the previous file hash, retain a verified backup of existing content, verify the result, and restore the backup on failure. `hive backup list` and `hive backup show BACKUP_ID` inspect retained backups. To restore one, create a new task and run `hive backup plan-restore BACKUP_ID TASK_ID --workspace PATH`; approve and execute that plan normally. Fedora templates support package install/remove, metadata refresh, system upgrade, service start/stop/enable/disable, and opening a firewall port. They use `sudo -n` and fail if noninteractive sudo is unavailable. Tests never perform system changes.

```bash
.venv/bin/hive plan install-package TASK_ID nginx --start-service
.venv/bin/hive plan remove-package TASK_ID nginx
.venv/bin/hive plan service TASK_ID start nginx
.venv/bin/hive plan open-firewall-port TASK_ID 8080
```

Each plan requires a new task at the intake phase. `hive failed list`, `hive failed show ID`, and `hive failed repair ID` support inspection and a linked repair task; repair still needs a new reviewed plan.

## Chat, research, and agents

`hive chat` uses the configured local Ollama model. Model text is never executed. Cloud fallback requires an enabled config, an API key environment variable, an endpoint and model, and `--allow-cloud` on the request.

```bash
.venv/bin/hive chat "Hello"
.venv/bin/hive research "What does dnf do?" --man dnf
.venv/bin/hive research "What changed?" --url https://example.com/
.venv/bin/hive agents create "Investigate nginx"
.venv/bin/hive agents list
.venv/bin/hive grid
```

Research reads only sources you name. Page text is treated as untrusted evidence and shown with source URLs. The summary is a short extract of retrieved text, so check the cited source before acting on it. `hive chat` can answer in English or Roman Urdu and mirrors the detected language.

Set `HIVE_BRAVE_API_KEY` to use the optional `--search "query"` flag with `hive research` or `hive operator propose`. Search is requested only when that flag is supplied; snippets are treated as untrusted sources.

For a guided Fedora system operation, `hive operator propose TASK_ID` asks the local model for clarification questions, options, and a recommendation. Supply `--man`, `--package`, or `--url` to include explicit research sources. Answer questions with `hive operator answer TASK_ID "..."`, then propose again. Review the result with `hive operator show TASK_ID`, record a discussion note with `hive operator discuss TASK_ID "..."`, and use `hive operator plan TASK_ID` to create a deterministic plan and approvals. The model can only propose supported Fedora operations; it cannot supply arbitrary commands. `hive run PLAN_ID` still requires the approvals.

## Browser

Enable `browser.enabled` in the config. Browser plans use a fresh, isolated Playwright context and public HTTPS URLs. Actions can visit, read, follow a link, fill, submit, upload, or download. Fill text is read from an environment variable at run time; the plan stores its hash. Uploads are bound to file hashes. Downloads stay within the approved workspace. Payment paths and selectors are blocked.

```bash
.venv/bin/hive task create "Read a page"
.venv/bin/hive browser plan TASK_ID https://example.com/
.venv/bin/hive approval pending
.venv/bin/hive browser show PLAN_ID
.venv/bin/hive browser run PLAN_ID
```

Use `--actions-file actions.json` for multiple actions. For example, `[ {"kind": "visit"}, {"kind": "read"} ]`. Forms and downloads require step approval. Browser sessions and pages close after the run, including failures.

## Voice

Enable `voice.enabled`, install the voice extra, and explicitly download an STT model. `hive voice download-tts-voices` downloads the English lessac and Urdu fasih voices into the Hive data directory; put the printed paths in `voice.tts_voice_en` and `voice.tts_voice_ur`. Use `hive listen` to record/transcribe or `hive talk` to transcribe, chat, and synthesize a spoken reply. `hive speak "Hello" --output reply.wav` writes speech to a WAV file. Spoken approval only identifies a request; Hive requires the matching approval ID to be typed before a decision.

After setup, check the microphone and speaker from your desktop session:

```bash
.venv/bin/hive listen --seconds 5
.venv/bin/hive speak "Hello from Hive" --output /tmp/hive-hello.wav --play
```

Speak during the five-second recording window. `hive talk` also needs the configured Ollama model running; `listen` and `speak` work without it. Roman Urdu synthesis uses a small local word map and may mispronounce unfamiliar words.

## GUI and screens

Enable `gui.enabled` and install the GUI tools reported by `hive doctor`. Create an agent, then a virtual display for its task. GUI plans cover an isolated Firefox launch, click, typing from an environment variable, and allowlisted keys. Every GUI action requires a plan and step approval. A click uses a captured screenshot as its approved reference and checks the target region against a fresh screenshot before acting.

```bash
.venv/bin/hive agents create "Inspect a site"
.venv/bin/hive screen create AGENT_ID
.venv/bin/hive gui plan-launch SCREEN_ID https://example.com/
.venv/bin/hive approval pending
.venv/bin/hive gui run PLAN_ID
.venv/bin/hive screen capture SCREEN_ID
.venv/bin/hive task create "Click a target" --agent-id AGENT_ID
.venv/bin/hive gui plan-click SCREEN_ID 200 120 SCREENSHOT_PATH --task-id NEW_TASK_ID
.venv/bin/hive gui run PLAN_ID
.venv/bin/hive screen close SCREEN_ID
```

The screen is an agent session so later approved tasks can use it. Close it explicitly when finished; `hive watchdog cleanup` closes sessions after 30 minutes of inactivity. GUI action failure closes its screen immediately. GUI input is confined to the virtual X11 display; the Wayland and accessibility adapters remain extension interfaces.

## Recovery and diagnostics

```bash
.venv/bin/hive doctor
.venv/bin/hive watchdog scan
.venv/bin/hive watchdog cleanup
.venv/bin/hive watchdog run
.venv/bin/hive resources leaks
.venv/bin/hive metrics
.venv/bin/hive task interrupted
```

The watchdog closes expired virtual displays, removes expired locks, and reconciles dead tracked processes. `hive watchdog run` repeats cleanup at the configured interval until stopped. It reports other stale resources for inspection. See [architecture and limits](docs/architecture.md) and [implementation status](docs/implementation_status.md) for verified features and live-runtime checks.
