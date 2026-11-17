# Hive AI — Project Guide

A map of the code: **which folder and file holds what, and what the main classes and functions
do**. Use it to find where to make a change.

Last updated: 2026-11-17 · ~6,900 lines in `hive/` · 71 tests in `tests/`

Related files: [DISCUSSION.md](DISCUSSION.md) · [TRACKING.md](TRACKING.md) ·
[architecture.md](architecture.md)

---

## 1. Top-level layout

| Path | What it is |
|---|---|
| `hive/` | The Python package (all source code) |
| `tests/unit/`, `tests/integration/` | pytest tests (fakes for models, browser, screens) |
| `docs/` | The four project docs: `DISCUSSION`, `TRACKING`, `architecture`, `PROJECT_GUIDE` |
| `config.example.yaml` | Example config; copy to `~/.config/hive/config.yaml` |
| `pyproject.toml` | Package and dependencies; extras: `dev`, `browser`, `voice` |
| `README.md` | Install and CLI usage |

Run: `.venv/bin/hive <command>` · Test: `.venv/bin/python -m pytest`

## 2. Package by package

### `hive/` root

| File | Contents |
|---|---|
| `__main__.py` | Lets `python -m hive` call `cli.app.main` |
| `paths.py` | `HivePaths` (config, data, state dirs; `database_file`, `logs_dir`) and `resolve_paths()`, which honours the `HIVE_*_DIR` env vars |

### `hive/config/` — configuration

| File | Contents |
|---|---|
| `schema.py` | Strict pydantic models for every config section: `CoreConfig`, `PathConfig`, `ModelsConfig` (`LocalModelConfig`, `RouterModelConfig`, `CloudFallbackConfig`), `MultiAgentConfig`, `RetryConfig`, `ResourceManagementConfig`, `ApprovalConfig`, `SecurityConfig`, `BrowserConfig`, `GuiConfig`, `VoiceConfig`, `TimeoutConfig`, `MemoryConfig`, `TriggerConfig`, and the root `HiveConfig` |
| `loader.py` | `load_config(path)`: safe YAML load, then validation into `HiveConfig` |

### `hive/core/` — tasks, approvals, workflow

| File | Main items | What they do |
|---|---|---|
| `models.py` | `Task`, `ApprovalRequest`, enums `TaskStatus`, `TaskPhase`, `RiskLevel`, `ApprovalStatus`, `LanguageCode`; `utc_now()`, `new_id()` | Shared domain records |
| `state.py` | `open_database(file)` | Opens SQLite in WAL mode |
| `checkpoint.py` | `TaskStore`: `create`, `get`, `list`, `transition`, `pause`, `resume`, `events`, `checkpoint`, `patch_state` | Persistent task state machine. Every change writes an event, and illegal phase jumps raise `TransitionError` |
| `approvals.py` | `ApprovalStore`: `add`, `get`, `list`, `decide`, `history` | Expiring approval queue with history; expired requests can't be approved |
| `operator.py` | `OperatorProposal`, `ProposedAction`; `propose()`, `answer_clarification()`, `discuss()`, `make_reviewable_plan()` | Guided workflow. `propose` matches skills, runs research, asks the model for a JSON proposal and moves the task to CLARIFY or DISCUSS. `make_reviewable_plan` turns an allowlisted Fedora action into a `CommandPlan` with approvals. **The future manager agent replaces or extends this file** |
| `orchestrator.py` | `prepare_task_for_approval()`, `start_execution()`, `finish_execution()` | Moves a task through the phases around plan creation and execution, and fires triggers |
| `diffs.py` | `unified_diff()`, `file_diff()` | Read-only diffs for plan review |
| `errors.py` | `HiveError`, `ConfigurationError`, `StateError` | Base exceptions |

### `hive/models/` — LLM providers

| File | Main items | What they do |
|---|---|---|
| `provider.py` | `ModelRequest`, `ModelResponse`, `ModelProvider` (`ping`, `complete`, `stream`) | Provider interface. **No tool-calling fields yet** |
| `ollama_provider.py` | `OllamaProvider` | Local Ollama chat API |
| `openai_compatible_provider.py` | `OpenAICompatibleProvider` | Any `/chat/completions` endpoint (OpenAI, DeepSeek, OpenRouter, …) |
| `router.py` | `ModelRouter.complete()`, `ModelUnavailableError` | Local-first routing with concurrency limits, redaction and cache; cloud only when allowed |
| `structured.py` | `structured_response(router, request, schema)` | Parses model JSON into a pydantic schema, with one repair retry |
| `cache.py` | `ModelCache` (`key`, `get`, `put`) | SQLite response cache |

### `hive/agents/` — workers

| File | Main items | What they do |
|---|---|---|
| `registry.py` | `Agent`, `AgentStatus`, `AgentRegistry` (`add`, `get`, `list`, `update`) | Agent records in SQLite (used by `hive agents`, `hive grid`) |
| `scheduler.py` | `LockStore` (`acquire`, `release`, `renew`, `prune_expired`, `hold`), `Scheduler.run(jobs)`, `LockBusyError` | Cross-process locks with heartbeat; bounded parallel job runner. **`Scheduler` is not used yet**; this is where the sub-agent runtime will plug in |

### `hive/execution/` — running approved plans

| File | Main items | What they do |
|---|---|---|
| `command_plan.py` | `CommandPlan` (+`digest`), `ExecutionStep`, `VerificationCheck`, `PlanStore` (`add`, `get`, `claim_run`, `finish_run`); builders `plan_file_write()`, `plan_commands()`, `plan_command_checks()` | Immutable, digest-bound plans. `PlanStore.add` also creates the approval requests |
| `executor.py` | `execute_plan(plan_id)`, `run_command(argv)`, `verify(check)`, `_approval_guard`, `_execute_approved_plan`, `_write_file_step` | Claims a plan, checks approvals and policy, runs argv commands (PTY optional), writes files atomically with backup, verifies, retries only safe steps, rolls back and records failures |
| `backups.py` | `BackupStore` (`create`, `get`, `list`, `verified_content`, `restore_plan`) | Checksum-verified file backups; restore is a new plan |

### `hive/policy/` — what is allowed

| File | Main items | What they do |
|---|---|---|
| `risk.py` | `classify_command(argv)`, `classify_file_write(path)`, `PolicyDecision` | Allows only known Fedora command shapes; gives each a risk level |
| `templates.py` | `package_install`, `package_remove`, `package_refresh`, `system_upgrade`, `service_start`, `service_action`, `firewall_add_port`, plus `*_check` helpers | Builds argv from validated inputs; never from model text |
| `command_parser.py` | `parse_command(text)` | Rejects shell operators (`;`, `|`, `&&`, `>` …), then splits text into argv with `shlex` |

### `hive/os_adapters/` — distro specifics

| File | Contents |
|---|---|
| `base.py` | `OSAdapter` interface (`install_package`, `remove_package`, `service`) |
| `fedora.py` | `FedoraAdapter`: dnf/systemctl/firewall-cmd plans built from `policy/templates.py` |
| `detector.py` | `detect_distro()` reads `/etc/os-release` |

### `hive/research/` — read-only knowledge

| File | Main items | What they do |
|---|---|---|
| `manager.py` | `research(request)`, `ResearchRequest`, `ResearchSummary`, `Citation`; sources `man_page()`, `package_metadata()`, `read_page(url)`, `BraveSearchProvider`; `page_text()`, `evidence_context()` | Collects man pages, RPM info, public HTTPS pages and Brave search results; marks everything untrusted and returns a short summary with citations |

### `hive/memory/` — learned procedures (Addendum A)

| File | Main items | What they do |
|---|---|---|
| `models.py` | `Skill`, `SkillStep`, `SkillParameter`, `SkillVersion`, `EntityMemory`, `TriggerRule`, `ImprovementProposal`; `reject_secrets()` | Validated memory records; secret-like values are rejected |
| `store.py` | `MemoryStore`: CRUD for skills (with versions), entities, triggers, trigger events, improvements | SQLite storage for memory |
| `manager.py` | `match_skills()`, `match_decision()`, `resolve_parameters()`, `SkillManager` (`draft_from_task`, `create_plan`, `propose_improvement`, `approve_improvement`, `restore_version`, …) | Matches a request to saved skills by word overlap, binds parameters (explicit → default → entity) and compiles a skill into a new reviewable plan |
| `triggers.py` | `TriggerEngine` (`add`, `on_event`, `execute_approved`) | Event triggers (task done or failed, approval pending, …) with one-shot delivery; power actions need approval |

### `hive/browser/` — isolated browser

| File | Main items | What they do |
|---|---|---|
| `plan.py` | `BrowserAction`, `BrowserPlan`, `create_browser_plan()`, `BrowserPlanStore` | Reviewable browser plans (visit, read, click, fill, submit, upload, download) |
| `manager.py` | `BrowserManager`, `PlaywrightBrowserManager` (`navigate`, `read`, `click_link`, `fill`, `submit`, `upload`, `download`, `screenshot`, `close`) | Fresh Playwright context per plan; network route guard |
| `executor.py` | `execute_browser_plan(plan_id)` | Approval check → run actions → screenshots → guaranteed close |
| `safety.py` | `validate_public_url()`, `url_is_public_https()`, `browser_risk()` | Blocks private, non-HTTPS and payment URLs |

### `hive/screens/` and `hive/gui/` — virtual displays and GUI input

| File | Main items | What they do |
|---|---|---|
| `screens/virtual_display.py` | `XvfbBackend` (`start`, `stop`, `screenshot`, `launch_firefox`), ownership checks | Starts and verifies Xvfb displays and Firefox |
| `screens/manager.py` | `ScreenManager`, `LocalScreenManager` (`create`, `screenshot`, `launch_firefox`, `close`, `cleanup_expired`) | Per-agent screens with a 30-minute lease |
| `gui/plan.py` | `GuiPlan`, `create_gui_plan()`, `GuiPlanStore` | Single-action GUI plans (click, type, key, launch) |
| `gui/executor.py` | `execute_gui_plan(plan_id)` | Approval and screenshot check, then input |
| `gui/x11.py` | `X11Adapter` (`click`, `type_text`, `key`, `screenshot`) | xdotool input on a virtual display |
| `gui/vision.py` | `target_confidence()`, `image_size()` | ImageMagick compare: is the click target still there? |
| `gui/adapter.py`, `gui/wayland.py`, `gui/accessibility.py` | Interfaces / stubs | Wayland and accessibility are not implemented |

### `hive/voice/` — speech

| File | Main items | What they do |
|---|---|---|
| `capture.py` | `record_wav(seconds)` | Push-to-talk recording via `pw-record` |
| `stt.py` | `FasterWhisperSTT` | Speech to text |
| `worker.py` | `main()` | Isolated subprocess for whisper inference |
| `tts.py` | `PiperTTS` | Text to speech |
| `pipeline.py` | `VoicePipeline` (`listen`, `transcribe`, `speak`) | Ties capture, STT, language mirroring and TTS together |
| `language.py` | `detect_language(text)` | English vs Roman Urdu hint |
| `transliterate.py` | `urdu_to_roman()`, `roman_to_urdu()` | Offline Urdu-script ↔ Roman conversion |
| `approval.py` | `approve_from_voice()` | Spoken approval also needs the typed request ID |

### `hive/resources/`, `hive/failures/`, `hive/observability/` — keeping it clean and visible

| File | Main items | What they do |
|---|---|---|
| `resources/manager.py` | `ResourceManager` (`acquire`, `release`, `cleanup_all`, `stale`), `ResourceHandle` | Tracks processes, browsers and screens owned by tasks |
| `resources/watchdog.py` | `scan()`, `cleanup()`, `run_periodic()` | Crash recovery for stale resources and locks |
| `failures/store.py` | `FailureStore`, `FailedTaskRecord`, `FailureClass` | Redacted failure context for `hive failed` |
| `observability/logger.py` | `redact()`, `configure_logger()` | JSONL logs with secret redaction (`redact` is used across the code) |
| `observability/audit.py` | `AuditLog.append()` | Append-only audit trail |
| `observability/metrics.py` | `MetricsStore` | Local aggregate metrics |
| `observability/doctor.py` | `diagnostics()`, `render_doctor()` | `hive doctor` dependency and state checks |

### `hive/cli/` — the `hive` command

| File | Contents |
|---|---|
| `app.py` (735 lines) | `_parser()` defines all commands; `main()` dispatches them. Groups: `init`, `config-check`, `config`, `task`, `plan`, `approval`, `run`, `agents`, `grid`, `failed`, `doctor`, `chat`, `listen`, `talk`, `speak`, `voice`, `browser`, `screen`, `gui`, `resources`, `watchdog`, `metrics`, `trace`, `research`, `operator`, `backup`, `demo` |
| `memory.py` | `skills`, `entities`, `triggers`, `improvements` command groups |
| `chat.py` | `build_router(config)`, `chat_once()`, `chat_loop()`: read-only chat |
| `voice.py` | `listen_command`, `speak_command`, model download helpers |
| `browser.py` | `install_chromium()` |
| `grid.py` | `render_grid()`: terminal grid of agents |
| `render.py` | Rich panels and tables for approvals and tasks |

## 3. Tests

| Folder | Covers |
|---|---|
| `tests/unit/` | approvals, cache, Fedora adapter, foundation, language, models/router, policy, research, resources, voice |
| `tests/integration/` | agents, browser (fake), checkpoints, CLI workflow, execution, GUI, high-risk approval, operator (fake model), screens, task memory, terminal/PTY, vision, watchdog |

## 4. Where to change what

| I want to… | Go to |
|---|---|
| Add a model provider (Anthropic, Gemini) or tool calling | `hive/models/provider.py`, new `*_provider.py`, `router.py`, `config/schema.py` |
| Build the manager agent loop | new module (e.g. `hive/manager/`), reusing `core/operator.py` and `core/checkpoint.py` |
| Run sub-agents in parallel | `hive/agents/scheduler.py` (`Scheduler`, `LockStore`) + `registry.py` |
| Allow a new kind of system action | `policy/templates.py` + `policy/risk.py` + `os_adapters/fedora.py` + `ProposedAction` in `core/operator.py` |
| Change approval rules | `core/approvals.py`, `execution/executor.py` (`_approval_guard`) |
| Add a research source | `research/manager.py` |
| Change how skills match or compile | `memory/manager.py` |
| Add a CLI command | `cli/app.py` (`_parser`, `main`) or a new `cli/*.py` module |
| Add a config option | `config/schema.py` and `config.example.yaml` |
