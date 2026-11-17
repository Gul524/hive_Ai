# Hive AI — Progress Tracking

This file shows **what is done, what is in progress, and what is left**. Update it at the end
of every phase or work session.

Last updated: 2026-11-17 · Branch: `main` · Last commit: `f9556e7` · Tests: **71 passed**

Related files: [DISCUSSION.md](DISCUSSION.md) (decisions) · [architecture.md](architecture.md) ·
[PROJECT_GUIDE.md](PROJECT_GUIDE.md)

Legend: ✅ done and tested · 🟡 code done, live check pending · ⚠️ partial · ⬜ not started ·
🗣️ under discussion

---

## 1. Overall status

| Area | Status | Done |
|---|---|---|
| Planning (discussion points 1–11) | 🗣️ Points 1–2 closed, Point 3 open | ~20% |
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
| 3 | How the manager reaches a heavy model | 🗣️ Open | Recommendation: hybrid (API manager + CLI coding agents) |
| 4 | Manager ↔ sub-agent protocol | ⬜ | |
| 5 | Sub-agent roster and models | ⬜ | |
| 6 | Memory design | ⬜ | |
| 7 | Interface (CLI/TUI/voice/GUI) | ⬜ | |
| 8 | Research sub-agent | ⬜ | |
| 9 | Coding sub-agent | ⬜ | |
| 10 | LMS + WhatsApp | ⬜ | |
| 11 | Master plan and phases | ⬜ | |

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

The final phase list comes with Point 11. This is the expected shape, so we can track it now.

| # | Work item | Status | Depends on |
|---|---|---|---|
| M1 | Provider layer: Anthropic, OpenAI, Gemini providers with **tool calling**; runtime connect/disconnect; free fallback | ⬜ | Point 3 |
| M2 | Router rework: "heavy first" for the manager, "cheap first" for sub-agents | ⬜ | M1 |
| M3 | Manager loop: conversation, tool calls (`ask_user`, `spawn_subagent`, `research`, `propose_plan`, `save_skill`) | ⬜ | M1, Point 4 |
| M4 | Sub-agent runtime: wire `Scheduler` + `LockStore`, run agents in parallel, return results | ⬜ | M3, Point 5 |
| M5 | Conversation memory and notes | ⬜ | Point 6 |
| M6 | Research sub-agent (live search, citations) | ⬜ | Point 8 |
| M7 | Coding sub-agent (CLI agents in a sandboxed project folder) | ⬜ | Point 9 |
| M8 | LMS browser skills (teach once, reuse) | ⬜ | Point 10, browser live |
| M9 | WhatsApp sub-agent | ⬜ | Point 10 |
| M10 | Documents and files sub-agent | ⬜ | |
| M11 | Voice, GUI, remote access | ⬜ | Last priority |

## 5. Known gaps and housekeeping

- [ ] `hive/cli/app.py` is 735 lines; split it by command group.
- [ ] The guided operator turns only allowlisted Fedora actions into plans.
- [ ] Skills compile only one deterministic action. Compound procedures are not supported.
- [ ] No `security/` package; no circuit breakers; no context compression.
- [ ] Runtimes missing on this PC: Ollama, Playwright Chromium, Xvfb, xdotool.
- [ ] `note.txt` contains only "hello" and can be removed.
- [x] Removed outdated docs (2026-11-17): `hive_ai_master_implementation_plan.md`,
  `docs/project_report.md`, `docs/implementation_status.md`, `docs/plan_addendum_a.md`.
  They remain in git history (commit `f9556e7`). The new master plan comes with Point 11.

## 6. Change log

| Date | Change |
|---|---|
| 2026-10-07 | Phases 0–16 implemented (commits `4073e59` → `38d2cf1`) |
| 2026-11-14 | Addendum A (task memory) committed (`f9556e7`). New goal defined: manager agent. Points 1 and 2 closed |
| 2026-11-17 | Point 3 opened (heavy model for the manager). These four docs created; outdated docs removed |
