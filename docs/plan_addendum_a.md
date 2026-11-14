# Plan Addendum A: Skill Memory, Task Templates, Entity Memory, Triggers, and Improvement Loop

This addendum extends the Hive AI master implementation plan. It does not replace any phase or weaken the existing risk engine, approval queue, verification, failure recording, or resource cleanup rules.

## Goal and boundaries

Hive can keep local, inspectable procedures for repeated tasks. A completed task can become a draft skill, and a user can add or edit a draft recipe. In guided discussion, “Remember this task,” “Save this as a skill,” and “Do it like this next time” defer draft creation until successful completion. A skill stays inactive until the user approves it. Each reuse creates a new task and a new immutable plan with new approvals. Skill content is data; it is never a shell script or a grant of execution authority.

Entity memory stores explicit task objects such as contacts, project paths, directories, URLs, and device names. It is distinct from general preference memory, which stays disabled by default. Secret-like names and values are rejected in skill, entity, trigger, and improvement records.

## Records and storage

Pydantic records: `Skill`, `SkillVersion`, `SkillParameter`, `SkillStep`, `EntityMemory`, `TriggerRule`, and `ImprovementProposal`. Skill states are draft, pending approval, active, disabled, and deprecated. Improvement states are pending, approved, denied, and applied. Skills carry versions, provenance, timestamps, success and failure counts, risk, verification, and cleanup descriptions.

SQLite WAL tables: `skills`, `skill_versions`, `skill_parameters`, `entities`, `triggers`, `improvement_proposals`, and `trigger_events`. Updates use transactions. Retained old skill versions stay available for rollback; restoration creates another version rather than overwriting history. `max_skill_versions` bounds retention and removes the oldest snapshot only when adding a new version.

## Matching and parameters

Before guided model proposal, Hive compares active skills against the task objective using intent/name words, English and Roman Urdu phrases, and prior success counts. High confidence is proposed; medium confidence is shown as a choice; low confidence continues ordinary planning. The scoring interface can accept a local embedding provider later. Only active skills are matched.

Skill parameters have names, descriptions, required flags, defaults, and optional entity types. Explicit values take precedence, then defaults, then an unambiguous matching entity. Missing required values are prompted in an interactive terminal or reported for the caller to supply. The CLI shows a final parameter summary before creating a plan. Values are bound into a fresh recipe; the planner validates the resulting action against the current policy.

## Execution and triggers

Current executable skill recipes compile to existing Fedora operator command plans, workspace file write plans, public HTTPS browser plans, or isolated GUI plans. One deterministic plan action is supported per skill recipe. Compound procedures and new tool types require their own reviewed plan adapter. The WhatsApp Web example can use browser selectors and environment-backed input, but live use still requires browser runtime and site-specific selectors. Flutter project creation needs a future approved adapter because `flutter create` is outside the current deterministic command policy.

Events: `task_completed`, `task_failed`, `agent_completed`, `all_agents_completed`, `approval_pending`, and `resource_cleaned`. Actions: notify, start task, pause task agent at a safe task checkpoint, allowlisted command plan, shutdown, reboot, and suspend. Command triggers create a normal plan and approval requests. Power actions use fixed `systemctl` argument vectors and require a separate current approval after the event. Each event is claimed once, so a retry does not fire a power action twice. Trigger-origin tasks do not recursively fire completion triggers.

## Improvement loop

An improvement is a proposal with a reason, evidence, patch, old version, and proposed version. A successful repair task linked to a failed skill task can create a proposal when its procedure differs. Browser and GUI skill failures create review proposals; the user must add concrete selector or verification changes before approval. The user may also propose a correction directly. Approval validates the new record, creates a new version, and leaves the previous version available. Denial leaves the active skill intact.

## CLI and verification

`hive skills` supports list, show, add, save-task, edit, approve, reject, disable, delete, match, plan, versions, and restore. `hive entities` supports add, list, show, update, and delete. `hive triggers` supports add, list, pending, disable, delete, and approved power action run. `hive improvements` supports propose, edit, list, show, approve, and deny. Rich panels show full skill and improvement proposals, parameters, versions, counts, and trigger approvals.

Automated tests cover skill creation and approval, phrase matching, required parameters, normal plan approval, trigger approval, command policy, entity CRUD, secret rejection, proposal approval, version restoration, and completion counts. Browser/GUI and power actions are not exercised against live external services or hardware in tests.
