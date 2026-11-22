# Hive AI — Progress Tracking

This file shows **what is done, what is in progress, and what is left**. Update it at the end
of every phase or work session.

Last updated: 2026-11-17 · Branch: `main` · Last commit: `f9556e7` · Tests: **71 passed**

Related files: [DISCUSSION.md](DISCUSSION.md) (decisions) · [architecture.md](architecture.md) ·
[PROJECT_GUIDE.md](PROJECT_GUIDE.md)

Legend: ✅ done and tested · 🟡 code done, live check pending · ⚠️ partial · ⬜ not started ·
🗣️ under discussion · 📝 drafted, waiting for discussion

---

## 1. Overall status

| Area | Status | Done |
|---|---|---|
| Planning (discussion points 1–12) | 🗣️ Points 1, 2, 7 and Settings closed; Point 3 open | ~30% |
| Safety kernel (tasks, approvals, executor, rollback, audit) | ✅ | ~95% |
| Old master plan, phases 0–16 + Addendum A (code) | ⚠️ | ~85% |
| Live verification against real runtimes | ⚠️ | ~35% |
| **Manager agent + sub-agents (new goal)** | ⬜ | ~0% |
| **Overall toward the new goal** | | **~30%** |

The old master plan built a strong, approval-driven **execution kernel**. The new goal (a
manager agent with sub-agents) reuses that kernel. Most of the remaining work is the manager
layer on top of it.

## 2. Discussion points

| # | Point | Status | Result |
|---|---|---|---|
| 1 | Scope, use cases, priorities | ✅ Closed 2026-11-14 | See DISCUSSION.md §4 |
| 2 | Cost: free first, paid optional | ✅ Closed 2026-11-14 | See DISCUSSION.md §4 |
| 3 | How the manager reaches a heavy model | 🗣️ Decided: hybrid · user has Claude Pro | Adjusted design waiting for confirmation: manager = `claude` CLI (Pro) with Hive tools via MCP; fallback = free Gemini key; research = free models |
| 4 | Manager ↔ sub-agent protocol | ⬜ | |
| 5 | Sub-agent roster and models | ⬜ | |
| 6 | Memory design | ⬜ | |
| 7 | Awareness and proactive help | ✅ Closed 2026-11-22 | 3 levels (context, agenda/daily brief, opportunities); Hive's own memory register; notification channel and brief time set in Settings (defaults: desktop + chat, 08:00) |
| — | Settings (cross-cutting) | ✅ Decided 2026-11-22 | All variables, keys and options managed from Hive's own Settings; keys in the Linux keyring, not YAML |
| 8 | Interface (CLI/TUI/voice/GUI) | ⬜ | |
| 9 | Research sub-agent | ⬜ | |
| 10 | Coding sub-agent | ⬜ | |
| 11 | LMS + WhatsApp | ⬜ | |
| 12 | Master plan and phases | ⬜ | |

## 3. Existing code: old master plan phases

| # | Phase | Status | Notes |
|---|---|---|---|
| 0 | Foundation (paths, config, logging, SQLite) | ✅ | |
| 1 | Rich UI, diffs, approval queue | ✅ | |
| 2 | Task state machine, checkpoints | ✅ | |
| 3 | Model hub (Ollama, OpenAI-compatible, cache) | 🟡 | Fake models only. **No Anthropic/Gemini providers, no tool calling, local-first router** |
| 4 | Clarification and research | 🟡 | No live search key |
| 5 | Policy and command safety | ✅ | |
| 6 | Execution engine (backup, rollback, PTY) | ✅ | |
| 7 | Resource manager and watchdog | ✅ | |
| 8 | OS adapters | ⚠️ | Fedora only |
| 9 | Multi-agent manager | ⚠️ | `Scheduler` exists but is **not used anywhere**. Agents are only DB records |
| 10 | Hive Grid | ✅ | |
| 11 | Failed tasks and repair | ✅ | |
| 12 | Observability and doctor | ✅ | |
| 13 | Voice (STT/TTS, Roman Urdu) | 🟡 | Live mic/speaker test pending |
| 14 | Browser (Playwright) | 🟡 | Chromium not installed |
| 15 | GUI and virtual screens | 🟡 | Xvfb, xdotool not installed |
| 16 | Testing and hardening | ⚠️ | No e2e or chaos tests |
| A | Task memory (skills, entities, triggers) | ✅ | Committed in `f9556e7` |

## 4. New goal: manager agent work items

The final phase list comes with Point 12. This is the expected shape, so we can track it now.

| # | Work item | Status | Depends on |
|---|---|---|---|
| M0 | **Settings**: `hive settings` (view, set, test), keyring storage for API keys, runtime reload; new sections for models, awareness, register, sub-agents, privacy | ⬜ | Settings decision |
| M1 | Provider layer: **Claude CLI provider** (headless, stream-json, session resume, usage-limit detection); Gemini and OpenAI-compatible providers with **tool calling**; runtime connect/disconnect; free fallback | ⬜ | Point 3 |
| M1b | **Hive MCP server**: exposes Hive's tools (register, research, propose_plan, ask_user, spawn_subagent) to the Claude CLI manager | ⬜ | M1, Point 4 |
| M2 | Router rework: "heavy first" for the manager, "cheap first" for sub-agents | ⬜ | M1 |
| M3 | Manager loop: conversation, tool calls (`ask_user`, `spawn_subagent`, `research`, `propose_plan`, `save_skill`) | ⬜ | M1, Point 4 |
| M4 | Sub-agent runtime: wire `Scheduler` + `LockStore`, run agents in parallel, return results | ⬜ | M3, Point 5 |
| M5 | Conversation memory and notes | ⬜ | Point 6 |
| M6 | Research sub-agent (live search, citations) | ⬜ | Point 9 |
| M7 | Coding sub-agent (CLI agents in a sandboxed project folder) | ⬜ | Point 10 |
| M8 | LMS browser skills (teach once, reuse) | ⬜ | Point 11, browser live |
| M9 | WhatsApp sub-agent | ⬜ | Point 11 |
| M10 | Documents and files sub-agent | ⬜ | |
| M10a | **Memory register**: SQLite store for todo, reminder, deadline, plan, goal and note items; CLI and manager tools to add, list, update and complete them | ⬜ | Point 7 |
| M11 | Awareness L1: situation snapshot (date, day, today's and overdue register items, running agents, PC state) in every manager prompt | ⬜ | M3, M10a |
| M12 | Awareness L2: background daemon, reminders from the register, daily brief, notifications | ⬜ | M10a, Point 7 |
| M13 | Awareness L3: daily opportunity finder from user goals | ⬜ | M6, Point 7 |
| M14 | Voice, GUI, remote access | ⬜ | Last priority |

## 5. Known gaps and housekeeping

- [ ] `hive/cli/app.py` is 735 lines; split it by command group.
- [ ] The guided operator turns only allowlisted Fedora actions into plans.
- [ ] Skills compile only one deterministic action. Compound procedures are not supported.
- [ ] No `security/` package; no circuit breakers; no context compression.
- [ ] Runtimes missing on this PC: Ollama, Playwright Chromium, Xvfb, xdotool.
- [ ] `note.txt` contains only "hello" and can be removed.
- [x] Removed outdated docs (2026-11-17): `hive_ai_master_implementation_plan.md`,
  `docs/project_report.md`, `docs/implementation_status.md`, `docs/plan_addendum_a.md`.
  They remain in git history (commit `f9556e7`). The new master plan comes with Point 12.

## 6. Change log

| Date | Change |
|---|---|
| 2026-10-07 | Phases 0–16 implemented (commits `4073e59` → `38d2cf1`) |
| 2026-11-14 | Addendum A (task memory) committed (`f9556e7`). New goal defined: manager agent. Points 1 and 2 closed |
| 2026-11-17 | Point 3 opened (heavy model for the manager). These four docs created; outdated docs removed |
| 2026-11-22 | Awareness requirement added: Point 7 drafted, work items M11–M13 added |
| 2026-11-22 | Point 7: memory register decided (Hive's own store for to-dos, reminders, deadlines, plans); work item M10a added |
| 2026-11-22 | Point 7 closed (notify and brief time via Settings). Settings decision added; work item M0 added |
| 2026-11-22 | Point 3: hybrid chosen; user has Claude Pro → manager through the `claude` CLI + Hive MCP server (M1b added), free Gemini fallback |
