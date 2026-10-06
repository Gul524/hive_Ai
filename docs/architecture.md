# Hive AI architecture and current limits

## Execution boundary

`hive plan` creates a validated `CommandPlan` and a digest of its serialized content. `PlanStore` saves the plan and creates approval requests with the command, diff, verification, and rollback summary. `hive run` reloads the saved plan, checks its digest, checks current plan and high-risk step approvals, claims the plan for a single run, and applies the command policy again. The executor never uses `shell=True`. File writes are confined to a workspace and block system paths. Fedora commands come from fixed templates.

The task store checkpoints each transition in SQLite. Before execution, a task remains at `wait_approval`. After a successful run it advances through execution, verification, report, and done. On failure after the plan is claimed, it becomes failed. Restarted or previously run plans do not resume automatically; create a new plan after inspecting the failure.

## Local state

SQLite uses WAL mode. Approval history, task events, plans, resources, agents, and failures live in the database. The JSONL audit log records approvals, execution, and completion. Logs and failure records redact common secret patterns. Files are created with user-only permissions where Hive controls creation. No external telemetry is configured.

An active subprocess is tracked as a resource. On timeout or cancellation, its process group is terminated and awaited. Resource cleanup is recorded, and `hive resources leaks` reports non-released handles. Browser, GUI, screen, and voice classes are interfaces for later implementation.

## Model boundary

The Ollama provider is local-first. Chat text is redacted before a model request. Cloud fallback needs four independent conditions: enabled in config, key in an environment variable, configured endpoint/model, and explicit `--allow-cloud` for that chat. Model output is displayed as text and never accepted as an executable command. Structured model output uses a Pydantic schema and permits one repair attempt.

## Known limits

- A live chat response cannot be verified without an Ollama service and the configured local model.
- Fedora package/service execution has been tested for plan generation and approval gating; no package was installed during development.
- The current CLI workflow provides one deterministic option for file and package tasks. Open-ended research, multiple-option discussion, and automated repair decisions need further work.
- Cross-process locks have a fixed lease and do not yet have a heartbeat. Long-running operations should use a lease longer than their timeout.
- The web page reader blocks obvious local addresses and disables redirects, but it is not a hardened general-purpose browser sandbox.
- Voice, browser, GUI, and virtual display interfaces are present but have no runtime adapters yet.
