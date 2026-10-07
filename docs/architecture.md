# Hive AI architecture and limits

## Execution boundary

The CLI turns a deterministic operation into a Pydantic plan and persists its digest. An approval is tied to the task, agent, plan ID, step ID, digest, and expiry. The executor reloads and claims the plan once, checks current approvals, then rechecks the command or action policy before execution. High-risk actions need a separate step approval. Subprocesses use argument vectors, never `shell=True`.

Fedora commands come from fixed templates. File writes check the previous content hash, stay inside an approved workspace, retain a checksum-verified backup, use an atomic replacement, and roll back on verification failure. A later restore is a new plan with new approval. Only idempotent low-risk commands may be retried, at most twice. Browser actions run in a fresh Playwright context; URL and payment checks are applied before and during execution. GUI actions run on an Xvfb display after screenshot and approval checks.

## State and recovery

SQLite uses WAL mode for tasks, checkpoint events, plans, approvals, agents, screens, resources, failures, locks, and aggregate metrics. The JSONL audit log records decisions and actions with redaction. `hive trace TASK_ID` shows checkpoint history. Failed runs save a redacted record and cleanup report; `hive failed repair ID` creates a linked task but cannot replay an old approval.

Subprocesses are tracked while they run and are terminated on timeout or cancellation. Browser contexts and pages close after each plan. A virtual screen is an agent session: it remains open for later approved GUI tasks until explicit close or its 30-minute lease expires. `hive watchdog cleanup` reconciles expired screens and locks and dead tracked processes. A running process that cannot be identified safely is reported for inspection rather than killed by PID alone.

## Model and research boundary

Local Ollama is the default model. Cloud fallback requires config, credentials, endpoint/model, and a per-request `--allow-cloud` flag. Model prompts are redacted and model output is displayed as text; no model output becomes an executable command. Structured model responses are Pydantic validated with one repair attempt.

Research can read explicit public HTTPS pages, local manual pages, and RPM package metadata. Optional Brave Search works with `HIVE_BRAVE_API_KEY` and an explicit `--search` query. Retrieved content is marked untrusted and cannot change the execution policy. The CLI extracts a short summary and lists sources. Search ranking follows the provider response; Hive does not independently verify snippets.

The guided operator path asks a model for a schema-validated proposal with clarification questions, options, and a recommendation. Only a small allowlist of Fedora action kinds can become deterministic plans. The user discusses the proposal, creates a plan, approves it, and then runs it. A model cannot directly execute or insert command arguments.

## Runtime requirements and limits

- Live chat, STT, TTS, Playwright, and Xvfb require external runtimes and model files. The test suite uses fakes for these integrations; no optional runtime or model is provisioned by the core install.
- Browser automation accepts public HTTPS sites only and blocks detected payment paths and selectors. It does not provide a general network sandbox against malicious public sites.
- GUI supports approved input in isolated virtual X11 sessions with Firefox. Host Wayland automation and accessibility control remain interfaces. A changed click target fails its confidence check.
- Search discovery requires an explicit query and a configured Brave API key; source snippets may be incomplete or outdated.
- Guided operator proposals require a running model and support a limited set of Fedora actions. They do not cover arbitrary natural-language requests.
- Stale handles for external processes whose ownership cannot be verified remain visible for manual inspection.
