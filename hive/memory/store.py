"""SQLite WAL storage for local skill, entity, trigger, and proposal records."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from hive.core.models import utc_now
from hive.core.state import open_database
from hive.memory.models import (EntityMemory, ImprovementProposal, ImprovementStatus,
                                MemoryError, Skill, SkillStatus, SkillVersion, TriggerRule)

T = TypeVar("T", bound=BaseModel)


class MemoryStore:
    def __init__(self, database_file: Path):
        self.database_file = database_file
        with open_database(database_file) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY, status TEXT NOT NULL, record_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS skill_versions (
                    skill_id TEXT NOT NULL, version INTEGER NOT NULL, record_json TEXT NOT NULL,
                    PRIMARY KEY(skill_id, version));
                CREATE TABLE IF NOT EXISTS skill_parameters (
                    skill_id TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL,
                    record_json TEXT NOT NULL, PRIMARY KEY(skill_id, version, name));
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY, entity_type TEXT NOT NULL,
                    name TEXT NOT NULL, record_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS triggers (
                    trigger_id TEXT PRIMARY KEY, event TEXT NOT NULL, enabled INTEGER NOT NULL,
                    record_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS improvement_proposals (
                    proposal_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, status TEXT NOT NULL,
                    record_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trigger_events (
                    trigger_id TEXT NOT NULL, event_key TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(trigger_id, event_key));
            """)

    def _get(self, table: str, key: str, value: str, model: type[T]) -> T:
        with open_database(self.database_file) as db:
            row = db.execute(f"SELECT record_json FROM {table} WHERE {key}=?", (value,)).fetchone()
        if row is None:
            raise MemoryError(f"Unknown {table[:-1]}: {value}")
        return model.model_validate_json(row[0])

    def _list(self, table: str, model: type[T]) -> list[T]:
        with open_database(self.database_file) as db:
            rows = db.execute(f"SELECT record_json FROM {table}").fetchall()
        return [model.model_validate_json(row[0]) for row in rows]

    def add_skill(self, skill: Skill) -> Skill:
        if skill.status not in {SkillStatus.DRAFT, SkillStatus.PENDING_APPROVAL} or skill.version != 1:
            raise MemoryError("New skill must start as version 1 draft or pending approval")
        with open_database(self.database_file) as db:
            db.execute("INSERT INTO skills VALUES (?, ?, ?)",
                       (skill.skill_id, skill.status.value, skill.model_dump_json()))
            self._snapshot(db, skill)
        return skill

    @staticmethod
    def _snapshot(db, skill: Skill) -> None:
        version = SkillVersion(skill_id=skill.skill_id, version=skill.version, snapshot=skill)
        db.execute("INSERT INTO skill_versions VALUES (?, ?, ?)",
                   (skill.skill_id, skill.version, version.model_dump_json()))
        for parameter in skill.parameters:
            db.execute("INSERT INTO skill_parameters VALUES (?, ?, ?, ?)",
                       (skill.skill_id, skill.version, parameter.name, parameter.model_dump_json()))

    def get_skill(self, skill_id: str) -> Skill:
        return self._get("skills", "skill_id", skill_id, Skill)

    def list_skills(self) -> list[Skill]:
        return self._list("skills", Skill)

    def skill_versions(self, skill_id: str) -> list[SkillVersion]:
        self.get_skill(skill_id)
        with open_database(self.database_file) as db:
            rows = db.execute("SELECT record_json FROM skill_versions WHERE skill_id=? ORDER BY version DESC",
                              (skill_id,)).fetchall()
        return [SkillVersion.model_validate_json(row[0]) for row in rows]

    def set_skill_status(self, skill_id: str, status: SkillStatus) -> Skill:
        if status not in {SkillStatus.ACTIVE, SkillStatus.DISABLED, SkillStatus.DEPRECATED}:
            raise MemoryError("Unsupported skill status change")
        with open_database(self.database_file) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
            if not row:
                raise MemoryError(f"Unknown skill: {skill_id}")
            skill = Skill.model_validate_json(row[0])
            if status == SkillStatus.ACTIVE and skill.status not in {SkillStatus.DRAFT, SkillStatus.PENDING_APPROVAL}:
                raise MemoryError("Only a proposed skill can be approved")
            skill.status = status
            skill.updated_at = utc_now()
            db.execute("UPDATE skills SET status=?, record_json=? WHERE skill_id=?",
                       (status.value, skill.model_dump_json(), skill_id))
        return skill

    def replace_draft(self, skill: Skill) -> Skill:
        with open_database(self.database_file) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json FROM skills WHERE skill_id=?",
                             (skill.skill_id,)).fetchone()
            if not row:
                raise MemoryError("Unknown skill")
            current = Skill.model_validate_json(row[0])
            if current.status not in {SkillStatus.DRAFT, SkillStatus.PENDING_APPROVAL} \
                    or skill.version != current.version or skill.status != current.status:
                raise MemoryError("Only an unchanged-version draft can be edited")
            skill.created_at = current.created_at
            skill.updated_at = utc_now()
            db.execute("UPDATE skills SET record_json=? WHERE skill_id=?",
                       (skill.model_dump_json(), skill.skill_id))
            db.execute("DELETE FROM skill_versions WHERE skill_id=? AND version=?",
                       (skill.skill_id, skill.version))
            db.execute("DELETE FROM skill_parameters WHERE skill_id=? AND version=?",
                       (skill.skill_id, skill.version))
            self._snapshot(db, skill)
        return skill

    def delete_skill(self, skill_id: str) -> None:
        with open_database(self.database_file) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("DELETE FROM skills WHERE skill_id=?", (skill_id,)).rowcount != 1:
                raise MemoryError(f"Unknown skill: {skill_id}")
            db.execute("DELETE FROM skill_versions WHERE skill_id=?", (skill_id,))
            db.execute("DELETE FROM skill_parameters WHERE skill_id=?", (skill_id,))
            db.execute("DELETE FROM improvement_proposals WHERE skill_id=?", (skill_id,))

    def record_skill_result(self, skill_id: str, *, success: bool) -> Skill:
        with open_database(self.database_file) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
            if not row:
                raise MemoryError(f"Unknown skill: {skill_id}")
            skill = Skill.model_validate_json(row[0])
            if success:
                skill.success_count += 1
            else:
                skill.failure_count += 1
            skill.last_used_at = utc_now()
            skill.updated_at = utc_now()
            db.execute("UPDATE skills SET record_json=? WHERE skill_id=?",
                       (skill.model_dump_json(), skill_id))
        return skill

    def apply_version(self, skill: Skill, *, max_versions: int = 20) -> Skill:
        if max_versions < 2:
            raise MemoryError("At least two versions are needed for rollback")
        with open_database(self.database_file) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json FROM skills WHERE skill_id=?", (skill.skill_id,)).fetchone()
            if not row:
                raise MemoryError(f"Unknown skill: {skill.skill_id}")
            current = Skill.model_validate_json(row[0])
            if skill.version != current.version + 1:
                raise MemoryError("Skill version must increment exactly once")
            versions = [row[0] for row in db.execute(
                "SELECT version FROM skill_versions WHERE skill_id=? ORDER BY version",
                (skill.skill_id,)).fetchall()]
            for old_version in versions[:max(0, len(versions) - max_versions + 1)]:
                db.execute("DELETE FROM skill_versions WHERE skill_id=? AND version=?",
                           (skill.skill_id, old_version))
                db.execute("DELETE FROM skill_parameters WHERE skill_id=? AND version=?",
                           (skill.skill_id, old_version))
            self._snapshot(db, skill)
            db.execute("UPDATE skills SET status=?, record_json=? WHERE skill_id=?",
                       (skill.status.value, skill.model_dump_json(), skill.skill_id))
        return skill

    def get_version(self, skill_id: str, version: int) -> SkillVersion:
        with open_database(self.database_file) as db:
            row = db.execute("SELECT record_json FROM skill_versions WHERE skill_id=? AND version=?",
                             (skill_id, version)).fetchone()
        if not row:
            raise MemoryError("Unknown skill version")
        return SkillVersion.model_validate_json(row[0])

    def add_entity(self, entity: EntityMemory) -> EntityMemory:
        with open_database(self.database_file) as db:
            db.execute("INSERT INTO entities VALUES (?, ?, ?, ?)",
                       (entity.entity_id, entity.entity_type, entity.name, entity.model_dump_json()))
        return entity

    def get_entity(self, entity_id: str) -> EntityMemory:
        return self._get("entities", "entity_id", entity_id, EntityMemory)

    def list_entities(self) -> list[EntityMemory]:
        return self._list("entities", EntityMemory)

    def update_entity(self, entity_id: str, *, name: str | None = None,
                      value: str | None = None, description: str | None = None) -> EntityMemory:
        with open_database(self.database_file) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT record_json FROM entities WHERE entity_id=?",
                             (entity_id,)).fetchone()
            if not row:
                raise MemoryError(f"Unknown entity: {entity_id}")
            data = EntityMemory.model_validate_json(row[0]).model_dump(mode="python")
            for key, changed in (("name", name), ("value", value), ("description", description)):
                if changed is not None:
                    data[key] = changed
            data["updated_at"] = utc_now()
            entity = EntityMemory.model_validate(data)
            db.execute("UPDATE entities SET name=?, record_json=? WHERE entity_id=?",
                       (entity.name, entity.model_dump_json(), entity_id))
        return entity

    def delete_entity(self, entity_id: str) -> None:
        with open_database(self.database_file) as db:
            if db.execute("DELETE FROM entities WHERE entity_id=?", (entity_id,)).rowcount != 1:
                raise MemoryError(f"Unknown entity: {entity_id}")

    def add_trigger(self, trigger: TriggerRule) -> TriggerRule:
        with open_database(self.database_file) as db:
            db.execute("INSERT INTO triggers VALUES (?, ?, ?, ?)",
                       (trigger.trigger_id, trigger.event.value, int(trigger.enabled), trigger.model_dump_json()))
        return trigger

    def get_trigger(self, trigger_id: str) -> TriggerRule:
        return self._get("triggers", "trigger_id", trigger_id, TriggerRule)

    def list_triggers(self) -> list[TriggerRule]:
        return self._list("triggers", TriggerRule)

    def update_trigger(self, trigger: TriggerRule) -> None:
        with open_database(self.database_file) as db:
            if db.execute("UPDATE triggers SET enabled=?, record_json=? WHERE trigger_id=?",
                          (int(trigger.enabled), trigger.model_dump_json(), trigger.trigger_id)).rowcount != 1:
                raise MemoryError("Unknown trigger")

    def delete_trigger(self, trigger_id: str) -> None:
        with open_database(self.database_file) as db:
            if db.execute("DELETE FROM triggers WHERE trigger_id=?", (trigger_id,)).rowcount != 1:
                raise MemoryError("Unknown trigger")
            db.execute("DELETE FROM trigger_events WHERE trigger_id=?", (trigger_id,))

    def claim_trigger_event(self, trigger_id: str, event_key: str) -> bool:
        with open_database(self.database_file) as db:
            db.execute("INSERT OR IGNORE INTO trigger_events VALUES (?, ?, 'pending')",
                       (trigger_id, event_key))
            return db.execute("SELECT changes()").fetchone()[0] == 1

    def list_trigger_events(self) -> list[dict[str, str]]:
        with open_database(self.database_file) as db:
            rows = db.execute("SELECT trigger_id, event_key, status FROM trigger_events "
                              "ORDER BY trigger_id, event_key").fetchall()
        return [dict(zip(("trigger_id", "event_key", "status"), row)) for row in rows]

    def set_trigger_event_status(self, trigger_id: str, event_key: str, status: str) -> None:
        if status not in {"pending", "running", "completed", "failed"}:
            raise MemoryError("Invalid trigger event status")
        with open_database(self.database_file) as db:
            if db.execute("UPDATE trigger_events SET status=? WHERE trigger_id=? AND event_key=?",
                          (status, trigger_id, event_key)).rowcount != 1:
                raise MemoryError("Unknown trigger event")

    def add_improvement(self, proposal: ImprovementProposal) -> ImprovementProposal:
        with open_database(self.database_file) as db:
            db.execute("INSERT INTO improvement_proposals VALUES (?, ?, ?, ?)",
                       (proposal.proposal_id, proposal.skill_id, proposal.status.value,
                        proposal.model_dump_json()))
        return proposal

    def get_improvement(self, proposal_id: str) -> ImprovementProposal:
        return self._get("improvement_proposals", "proposal_id", proposal_id, ImprovementProposal)

    def list_improvements(self) -> list[ImprovementProposal]:
        return self._list("improvement_proposals", ImprovementProposal)

    def update_improvement(self, proposal: ImprovementProposal) -> None:
        with open_database(self.database_file) as db:
            if db.execute("UPDATE improvement_proposals SET status=?, record_json=? WHERE proposal_id=?",
                          (proposal.status.value, proposal.model_dump_json(), proposal.proposal_id)).rowcount != 1:
                raise MemoryError("Unknown improvement proposal")
