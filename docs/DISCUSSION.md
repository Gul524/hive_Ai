# Hive AI — Discussion and Decisions

This file records **what we decided, what we will do, and what we will not do**. It is the
single source of truth for decisions. When a discussion point closes, its result goes here
before any code is written.

Last updated: 2026-11-17

Related files: [TRACKING.md](TRACKING.md) (progress) · [architecture.md](architecture.md)
(how the system fits together) · [PROJECT_GUIDE.md](PROJECT_GUIDE.md) (which file does what)

---

## 1. The goal

Hive AI is a **personal manager agent** that runs on the user's PC.

- It talks with the user like a person, in Roman Urdu or English, by text now and by voice later.
- It sits **between the user and a team of sub-agents**. It splits work, hands parts to
  sub-agents (research, coding, browser, WhatsApp, files), collects results and reports back.
- **The user decides.** The manager presents options and a recommendation, discusses them,
  and acts only after the user approves.
- It can control the whole PC, but only through Hive's safety kernel (approval → execute →
  verify → rollback → audit).
- It **learns procedures**. The user teaches a task once (for example, checking attendance on
  the university portal), and Hive saves and reuses it.
- It runs locally for now. Remote access (talking to the manager from anywhere) comes later.

## 2. How we work (process)

1. Discuss **one point at a time**. Each point ends with a recommendation and a user decision.
2. Closed points are written in §4 of this file.
3. Once all points are closed → write a **master plan** → a coding agent implements it
   **phase by phase** → the user tests each phase before the next one starts.
4. Discussion language: Roman Urdu. Documentation and code: English.

## 3. Rules: do and don't

### Do

- Keep the **existing safety kernel** as the only way to change the system. Every action goes
  through plan → approval → executor → verification.
- Keep every model **swappable at runtime**. Any model, paid or free, can be connected or
  disconnected. Routing uses whatever is connected.
- Always have a **free path**. If no paid key is connected, Hive still works with free tiers
  or a local model.
- Treat web pages, files, messages and sub-agent output as **untrusted data**, never as
  instructions.
- Save learned procedures as **skills** that the user can inspect, edit and approve.
- Build in small phases, each with tests and a live check by the user.
- Update [TRACKING.md](TRACKING.md) after every phase.

### Don't

- Don't let a model run shell text directly. Models **propose**; Hive's deterministic plans
  **execute**.
- Don't use `shell=True`, and don't put secrets or API keys in logs, prompts or memory.
- Don't extract subscription login tokens (Claude Pro, ChatGPT Plus and so on) for use inside
  Hive. Use subscriptions only by running the **official CLIs** (`claude`, `codex`, `gemini`).
- Don't make Hive depend on one provider.
- Don't build voice, GUI or remote access before the manager, research and coding work
  (see the priority order in Point 1).
- Don't self-host large open models. This was ruled out as not free on this hardware.
- Don't retry dangerous or non-idempotent actions automatically.

## 4. Closed decisions

### Point 1: scope and priorities (closed 2026-11-14)

| Topic | Decision |
|---|---|
| Hardware | 16 GB RAM, 256 GB SSD, 12-core AMD CPU, no dedicated GPU |
| Models | Hybrid: a cloud model for heavy work, a small local model for small tasks |
| Users | Personal, for one user only |
| Platform | Linux (Fedora) first |
| WhatsApp | The user accepts the risk of an account ban from unofficial automation |
| Key requirement | **Learn once, reuse**: a taught procedure is saved as a skill and reused |

**Priority order**

1. Research and notes/memory
2. Coding (build, run, test, deploy, read analyzer reports)
3. LMS (a custom university website, browser only) and WhatsApp
4. Documents and files (Word/PDF, upload, organize)
5. Voice, GUI and remote access

### Point 2: cost (closed 2026-11-14, updated by Point 3)

- Hive must be **usable for free**. Paid models are optional and make it better; they are
  never required.
- Free options: free API tiers (Gemini, DeepSeek, Qwen, Kimi and others) plus a small local
  model.
- Models are dynamic: added, removed and swapped at runtime.

## 5. Open points

### Point 3: how the manager agent reaches a heavy model (OPEN)

**User direction (2026-11-17):** the main (manager) agent should be a heavy model reached with
the user's own provider access: Claude, Codex/OpenAI or Gemini Pro, whichever is connected.
It orchestrates sub-agents for tasks.

This loosens Point 2: the manager's heavy model is the user's choice, and a free fallback
(for example the Gemini free tier) keeps Hive usable without paid access.

**Options**

| Option | How it works | Pros | Cons |
|---|---|---|---|
| A. Direct API keys | Hive calls the Anthropic, OpenAI or Gemini API and runs the tool-calling loop itself | Full control. Every action passes through Hive's approvals. Provider-agnostic | Paid per token (Gemini has a limited free tier) |
| B. Official CLI agents | Hive runs `claude -p`, `codex exec` or `gemini` as sub-agents | Uses existing subscriptions. Gemini CLI is free with a Google login. Very strong at coding | These tools have their own tools and can bypass Hive's approvals unless they are restricted to a project folder |
| **C. Hybrid (recommended)** | Manager = A. Coding sub-agent = B | Safe, swappable manager, and heavy coding on a subscription | More work to build |

**Questions waiting for the user**

1. A, B or C?
2. Which access does the user have now: Claude Pro/Max, ChatGPT Plus, a Gemini API key,
   an Anthropic or OpenAI API key? This decides the default manager model.

Machine facts (checked 2026-11-17): `claude` CLI installed at `~/.local/bin/claude`;
`codex`, `gemini` and `ollama` are not installed.

### Upcoming points (not started)

| # | Topic |
|---|---|
| 4 | Manager ↔ sub-agent protocol: how tasks are handed out, how results come back, the task flow |
| 5 | Sub-agent roster: which sub-agents exist, which model each one uses, what tools each one gets |
| 6 | Memory: conversation memory, notes, user profile, skills, and what the manager remembers |
| 7 | Interface: CLI first? A chat TUI? When do voice and GUI come? |
| 8 | Research sub-agent design (search sources, free search APIs, citations) |
| 9 | Coding sub-agent design (CLI agent sandboxing, project folders, test/deploy) |
| 10 | LMS + WhatsApp design (browser skills, teach-once flow) |
| 11 | Master plan and phase breakdown |
