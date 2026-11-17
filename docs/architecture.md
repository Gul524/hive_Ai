# Hive AI — Architecture

This file explains **how Hive fits together**: what exists today, what the target looks like,
and the safety boundary that never changes.

Last updated: 2026-11-17

Related files: [DISCUSSION.md](DISCUSSION.md) · [TRACKING.md](TRACKING.md) ·
[PROJECT_GUIDE.md](PROJECT_GUIDE.md) (file-by-file detail)

---

## 1. Big picture

Hive has two layers:

1. **The brain (target, not built yet):** a manager agent on a heavy model that talks with the
   user and coordinates sub-agents.
2. **The kernel (built):** a safety-first execution engine. Nothing changes on the PC unless
   it becomes a stored, reviewed plan, gets an approval, and passes verification.

```
                         ┌──────────────────────────────┐
   User (text / voice) ─▶│        MANAGER AGENT         │  heavy model: Claude / GPT / Gemini
   Roman Urdu / English  │  talk · split work · discuss │  (whichever is connected; free fallback)
                         │  recommend · ask approval    │
                         └──────┬───────────────┬───────┘
                   spawn_subagent│               │propose_plan / ask_user
          ┌──────────────┬──────┴──────┬────────┴─────┬──────────────┐
          ▼              ▼             ▼              ▼              ▼
     Research        Coding       Browser/LMS     WhatsApp      Files/Docs     ← SUB-AGENTS
   (cheap/free     (claude/codex  (Playwright +   (browser)     (local tools)    run in parallel
     model)         CLI, sandbox)  saved skills)                                 (Scheduler + locks)
          │              │             │              │              │
          └──────────────┴──────┬──────┴──────────────┴──────────────┘
                                ▼  proposals only, never raw commands
   ┌────────────────────────────────────────────────────────────────────────────┐
   │ HIVE KERNEL (exists)                                                       │
   │ Task state machine → Plan (digest) → Approval queue → Executor → Verify    │
   │ → Rollback on failure → Failure record → Audit log → Resource cleanup      │
   │ Memory: skills · entities · triggers · improvement proposals              │
   └────────────────────────────────────────────────────────────────────────────┘
                                ▼
                    Fedora PC: packages, services, files, browser, GUI
```

Box status: the kernel exists and is tested. The manager, the sub-agent runtime and the
heavy-model providers are the next work (see [TRACKING.md](TRACKING.md) §4).

## 2. Layers and packages

| Layer | Package(s) | Status | Role |
|---|---|---|---|
| Interface | `hive/cli/`, `hive/voice/` | ✅ CLI, 🟡 voice | `hive` command; push-to-talk STT/TTS |
| Brain | `hive/core/operator.py`, `hive/models/` | ⚠️ | Today: one-shot proposal from a local model. Target: manager loop with tool calls |
| Agents | `hive/agents/` | ⚠️ | Registry and scheduler exist; nothing runs agents yet |
| Knowledge | `hive/research/`, `hive/memory/` | 🟡 / ✅ | Read-only research; skills, entities, triggers |
| Kernel | `hive/core/`, `hive/execution/`, `hive/policy/`, `hive/os_adapters/` | ✅ | Tasks, approvals, plans, execution, verification |
| Actuators | `hive/browser/`, `hive/gui/`, `hive/screens/` | 🟡 | Isolated browser, virtual X11 screens, GUI input |
| Safety ops | `hive/resources/`, `hive/failures/`, `hive/observability/` | ✅ | Cleanup, watchdog, failure records, audit, metrics, doctor |
| Foundation | `hive/config/`, `hive/paths.py`, `hive/core/state.py` | ✅ | Config schema, paths, SQLite WAL |

## 3. Task lifecycle (the kernel's state machine)

Every unit of work is a `Task` stored in SQLite with a checkpoint event for each change.

```
INTAKE → CLARIFY → RESEARCH → SUMMARIZE → OPTIONS → RECOMMEND → DISCUSS
       → WAIT_APPROVAL → PLAN → PREFLIGHT → EXECUTE → VERIFY → REPORT → DONE
                                              │
                                              └─▶ RECOVER (rollback) → FAILED
   Any phase → CANCELED
```

This matches the user's rule: **discuss → decide → act**. In the target design, the
manager agent drives tasks through these phases on the user's behalf, and sub-agent work
becomes child tasks.

## 4. Current flow (what works today)

```
hive task create "..."                      → Task (INTAKE)
hive operator propose TASK [--search/--url] → local model returns JSON proposal
                                              (questions, options, recommendation, action)
hive operator answer / discuss              → user input saved on the task
hive operator plan TASK                     → allowlisted action → CommandPlan + approvals
hive approval approve ID                    → approval bound to plan digest + expiry
hive run PLAN                               → executor re-checks approvals and policy,
                                              runs argv (no shell), verifies, rolls back
```

Browser and GUI actions have their own plan commands (`hive browser plan`, `hive gui plan-*`)
with the same approve-then-run rule.

## 5. Model layer

**Today:** [router.py](../hive/models/router.py) is **local-first**: it tries Ollama, and
falls back to one OpenAI-compatible cloud endpoint only if config allows it and the request
passes `allow_cloud`. Providers return plain text (no tool calling). JSON output is validated
by `structured_response` with one repair attempt. Responses are cached in SQLite.

**Target (Point 3, open):**

| Role | Model choice | Access |
|---|---|---|
| Manager | Heavy model: Claude / GPT / Gemini Pro | API key (recommended) |
| Research, summaries | Cheap or free: Gemini Flash, DeepSeek, Qwen, Kimi | Free API tiers |
| Coding | Claude Code / Codex / Gemini CLI | Official CLI, subscription or free login |
| Tiny local tasks | Small local model | Ollama (optional) |

Models are registered at runtime. The router picks the best **connected** model for each role
and falls back to a free one when a paid model is missing.

## 6. Safety boundary (never changes)

- **Models propose, the kernel executes.** Model output is data. Only deterministic plans built
  from fixed templates and validated fields can run. No `shell=True`; commands are argv lists.
- **Approvals are bound** to the task, agent, plan ID, step ID, plan digest and an expiry.
  The executor reloads and claims a plan once, then re-checks approvals and policy.
  High-risk steps need a separate step approval.
- **File writes** stay inside an approved workspace, check the previous content hash, keep a
  checksum-verified backup, replace atomically, and roll back if verification fails. A restore
  is a new plan with a new approval.
- **Retries** happen only for idempotent low-risk commands, at most twice.
- **Browser** runs in a fresh Playwright context, public HTTPS only, with payment paths and
  selectors blocked.
- **GUI** runs on an isolated Xvfb display after a screenshot confidence check and approval.
- **Untrusted content** (web pages, search results, messages) is marked untrusted and can never
  change policy.
- **Secrets** are redacted from logs, the audit trail, prompts and memory records.
- **Resources** (processes, browser contexts, screens, locks) are tracked and cleaned up; the
  watchdog reconciles leftovers after a crash.

New pieces (manager, sub-agents, CLI coding agents) must fit inside this boundary. CLI coding
agents are the one case that runs its own tools; they must be limited to a sandboxed project
folder.

## 7. Storage

| Store | Location | Contents |
|---|---|---|
| SQLite (WAL) | `~/.local/share/hive/hive.db` (see `hive/paths.py`) | tasks, events, plans, approvals, agents, locks, screens, resources, failures, metrics, model cache, skills, entities, triggers, improvements |
| Audit log | `audit.jsonl` in the state directory (`~/.local/state/hive/`) | Decisions and actions, redacted |
| Backups | data directory | Verified file backups for rollback |
| Config | `~/.config/hive/config.yaml` | Validated by `hive/config/schema.py` |

`HIVE_CONFIG_DIR`, `HIVE_DATA_DIR` and `HIVE_STATE_DIR` override the locations.

## 8. Current limits

- The guided operator maps only allowlisted Fedora actions to plans.
- Skills compile one deterministic action; compound procedures are not supported yet.
- Agents do not run in parallel; `Scheduler` is not wired in.
- Live runs need runtimes not installed here: Ollama, Playwright Chromium, Xvfb, xdotool.
- Wayland and accessibility GUI control are interfaces only.
- Search needs a Brave API key and an explicit `--search` query.
