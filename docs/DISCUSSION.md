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
- It is **aware** (added 2026-11-22). It always knows today's date, day and time, what work
  is pending, and what is running right now (tasks, sub-agents, approvals). It **tells the user
  on its own** about things that are useful to them: deadlines, reminders, and opportunities
  related to their goals. It only informs and suggests; acting still needs approval.
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
- Don't let awareness **act** on its own. Proactive means "tell and suggest", never "do".
- Don't watch the screen, camera or microphone in the background unless the user turns it on.

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

**User answers (2026-11-22):** 1 → **C (hybrid)**. 2 → the user has **Claude Pro** (a
subscription, no API key).

**What this changes:** Claude Pro does not include API access, so the manager cannot call the
Anthropic API directly (Option A needs a paid API key). Using the subscription's login token
inside Hive is against the rules (§3). The official way to use Pro is the `claude` CLI.

**Adjusted hybrid (recommended, waiting for confirmation)**

| Role | Runs on | How |
|---|---|---|
| Manager | **Claude (Pro) through the `claude` CLI in headless mode** | `claude -p --input-format stream-json --output-format stream-json`, with built-in tools turned off (`--tools ""`) and **Hive's tools given as an MCP server** (`--mcp-config … --strict-mcp-config`). Claude can then only act through Hive's tools, and every action still goes through Hive's approvals. The conversation continues with `--resume` |
| Manager fallback | Free Gemini API key (from Google AI Studio) or another free provider | Used when the Pro usage limit is reached or the user switches it in Settings |
| Coding sub-agent | `claude` CLI with its normal tools, **limited to one project folder** | The same Pro subscription |
| Research, summaries, awareness (L3) | Free models (Gemini Flash, DeepSeek, Qwen …) | API keys from free tiers, so Pro usage is saved for the manager and coding |
| Later | Anthropic, OpenAI or Gemini paid API keys | Plug in as API providers through Settings; no redesign needed |

**Caution:** Claude Pro has usage limits that reset every few hours. The manager and the coding
agent share them. Hive must show the usage, switch to the free fallback when the limit is hit,
and send light work to free models.

Checked on this PC (2026-11-22): `claude` CLI 2.1.330 supports all the flags above.

Machine facts (checked 2026-11-17): `claude` CLI installed at `~/.local/bin/claude`;
`codex`, `gemini` and `ollama` are not installed.

### Upcoming points (not started)

| # | Topic |
|---|---|
| 4 | Manager ↔ sub-agent protocol: how tasks are handed out, how results come back, the task flow |
| 5 | Sub-agent roster: which sub-agents exist, which model each one uses, what tools each one gets |
| 6 | Memory: conversation memory, notes, user profile, skills, and what the manager remembers |
| 7 | **Awareness and proactive help** (see draft below) |
| 8 | Interface: CLI first? A chat TUI? When do voice and GUI come? |
| 9 | Research sub-agent design (search sources, free search APIs, citations) |
| 10 | Coding sub-agent design (CLI agent sandboxing, project folders, test/deploy) |
| 11 | LMS + WhatsApp design (browser skills, teach-once flow) |
| 12 | Master plan and phase breakdown |

### Point 7 draft: awareness (requested 2026-11-22, not yet discussed)

The user wants Hive to be aware: it knows today's date and day, what work is pending, what is
happening right now, and it tells the user about work that benefits them.

**Recommended design: three levels**

| Level | What Hive knows or does | How | Cost |
|---|---|---|---|
| L1 Context (always on) | Date, day, time, timezone; pending tasks and reminders; running tasks and sub-agents; approvals waiting; basic PC state (battery, RAM, disk) | A "situation snapshot" is built locally and added to every manager prompt | Free, local |
| L2 Agenda and reminders | To-do list, deadlines (LMS assignments, classes), reminders, a **daily brief** (for example each morning: "aaj yeh kaam hain, yeh deadline kal hai") | A Hive background service (daemon) with a schedule; uses the existing trigger engine; desktop notification | Free, local plus a cheap model for wording |
| L3 Opportunities | Things useful for the user's goals: relevant news, new tools for current projects, follow-ups the user forgot | The research sub-agent runs once a day on the user's goals and interests from memory, then suggests 2–3 items | Cheap or free model plus search |

**Rules:** awareness informs and suggests only. Every action still goes through approval.
Screen, camera and microphone watching is off by default (unlike Stonic AI, where it is on).

**User answer (2026-11-22): Hive keeps its own memory register.** Everything is stored and
managed there: to-dos, reminders, deadlines and plans. Hive reads the register to tell the user
what to do and what is still left.

**Draft design of the register (to confirm)**

| Item type | Example | Key fields |
|---|---|---|
| `todo` | "Hive ka Point 3 decide karna" | title, status (open / doing / done / dropped), priority, due date |
| `reminder` | "Raat 9 baje assignment check karna" | time, repeat rule (once / daily / weekly), delivered yes/no |
| `deadline` | "DSA assignment, 25 Nov" | due date and time, source (user / LMS agent), linked task |
| `plan` | "Hive manager agent build plan" | steps (each with a status), progress %, linked to-dos |
| `goal` | "Flutter mein expert banna" | description; used by L3 to find useful things |
| `note` | an idea or a fact to remember | text, tags |

- **Storage:** local SQLite, the same `hive.db` as tasks and skills (a new `register` table
  group). No cloud and no secrets.
- **Who adds items:** the user in chat ("kal 5 baje yaad dilana…"), the manager when it plans
  work, and sub-agents when they find something (for example, the LMS agent finds a new
  deadline). Items added by an agent are marked with their source, and the user can see,
  edit or delete them.
- **How Hive uses it:** L1 puts "today and overdue" items into every manager prompt. L2 fires
  reminders and builds the daily brief. L3 reads `goal` items to find useful things.
- **Later:** an optional sync with Google Calendar or other sources. The register stays
  the main source.

**Questions and answers**

1. Where do tasks come from? → **Hive's own memory register.**
2. How should Hive notify? → **The user controls it in Settings.** Default: a desktop
   notification plus a message in the chat. Voice is added later as a Settings option.
3. When should the daily brief come, and how often do L3 suggestions come? → **The user controls
   it in Settings.** Default: the brief at 08:00 (or at first login if the PC was off), and L3
   once a day.

Point 7 is considered closed with these defaults unless the user changes them.

## 6. Cross-cutting decisions

### Settings (decided 2026-11-22)

Hive has **its own Settings**. Every variable, key and option is managed and controlled from
there, not by editing code.

| Settings section | Examples |
|---|---|
| Models and keys | Connect or disconnect Claude, OpenAI, Gemini or free providers; pick the manager model; free fallback on/off |
| Awareness | L1/L2/L3 on/off, daily brief time, L3 frequency, notification channel (desktop, chat, voice), quiet hours |
| Register | Default reminder lead time, auto-archive of done items |
| Sub-agents | Which ones are enabled, the model for each, the maximum number running in parallel |
| Safety | Approval expiry, risk levels that need a step approval, allowed workspaces |
| Privacy | Screen, camera and microphone watching (off by default), what may be sent to cloud models |
| Voice, browser, GUI | The existing options already in `config.yaml` |

**How it is stored (recommended):**
- Normal settings go in `~/.config/hive/config.yaml`, validated by `hive/config/schema.py`.
  This already exists, together with `hive config show` and `hive config edit`.
- **API keys and passwords never go in the YAML file.** They are stored in the Linux keyring
  (GNOME Keyring / Secret Service). Settings only show whether a key is connected and can test
  or remove it.
- Interface: first a `hive settings` command (view, set, test, connect key). Later a settings
  screen in the TUI/GUI (Point 8).
- Changes apply at runtime where possible (for example, connecting a new model), without
  restarting Hive.
