# Hive AI

Hive AI is a local-first Linux operator with persistent tasks, explicit approvals, and a constrained executor. Fedora is the first supported distribution. The [master implementation plan](hive_ai_master_implementation_plan.md) is the product specification.

## Install and initialize

Python 3.11 or newer is required.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/hive --help
.venv/bin/hive init
.venv/bin/hive doctor
.venv/bin/python -m pytest
```

`hive init` creates a SQLite database in WAL mode and a local JSONL log. Default locations are `~/.local/share/hive` and `~/.local/state/hive`. `XDG_*_HOME` or `HIVE_CONFIG_DIR`, `HIVE_DATA_DIR`, and `HIVE_STATE_DIR` override the defaults. Copy [config.example.yaml](config.example.yaml) to `~/.config/hive/config.yaml` to customize settings. `HIVE_*_DIR` takes precedence over paths in the config file.

## Safe file workflow

The following commands plan a write inside the current directory. `hive run` refuses the plan until the displayed approval is granted.

```bash
TASK_ID=$(.venv/bin/hive task create "Create a note" | sed -n 's/.*Task: \([0-9a-f]*\).*/\1/p')
.venv/bin/hive plan write-file "$TASK_ID" "$PWD/note.txt" --workspace "$PWD" --content "hello"
.venv/bin/hive approval pending
.venv/bin/hive approval show APPROVAL_ID
.venv/bin/hive approval approve APPROVAL_ID
.venv/bin/hive run PLAN_ID
.venv/bin/hive task show "$TASK_ID"
```

The plan output contains the real `PLAN_ID` and approval ID. File writes are limited to the approved workspace, block system directories, verify the previous file hash before writing, create a backup for an existing file, and restore it if verification fails. Plans are single use. Approval expires after the configured time even if it was previously granted.

For Fedora, `hive plan install-package TASK_ID nginx --start-service` creates a package and service plan. The plan and every high-risk step require separate approvals. The executor calls `sudo -n`, so it fails rather than waiting for a password. This repository's tests never install packages or start services.

## Chat and agents

`hive chat` uses the configured Ollama model. Start Ollama and install the model named in the config before expecting a live response. No model output is executed as a command. Optional cloud fallback requires the config setting, an API key environment variable, a cloud base URL and model, and `--allow-cloud` on the chat command. Browser and voice are disabled by default.

```bash
.venv/bin/hive chat "Hello"
.venv/bin/hive agents create "Investigate nginx"
.venv/bin/hive agents list
.venv/bin/hive grid
.venv/bin/hive failed list
.venv/bin/hive resources leaks
```

The CLI creates independent task and agent records. Package, service, and file execution use SQLite locks to prevent conflicting runs. Failed runs are saved under the data directory and can be inspected with `hive failed show ID`; `hive failed repair ID` creates a linked repair task that needs a new plan and approval.

## Current scope

The MVP includes the CLI, Rich review UI, task checkpoints, approvals, local model adapter, deterministic Fedora plans, safe file execution, verification, failure records, process cleanup, resource reports, and doctor diagnostics. Voice, browser, GUI, and virtual displays have interfaces only. Model chat needs a running Ollama instance; none is installed automatically. Web search has an interface, and the page reader treats retrieved material as untrusted evidence.

See [architecture and limitations](docs/architecture.md) for implementation details and remaining work.
