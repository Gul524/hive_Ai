from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from hive.core.models import new_id, utc_now
from hive.core.state import open_database


class AgentStatus(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    WAITING_APPROVAL = "waiting_approval"
    FAILED = "failed"


class Agent(BaseModel):
    agent_id: str = Field(default_factory=new_id)
    task_id: str | None = None
    objective: str
    status: AgentStatus = AgentStatus.IDLE
    workspace: Path
    logs_path: Path | None = None
    screen_path: Path | None = None
    latest_action: str = "created"
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())


class AgentRegistry:
    def __init__(self, database_file: Path):
        self.database_file = database_file
        with open_database(database_file) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS agents (agent_id TEXT PRIMARY KEY, status TEXT NOT NULL, record_json TEXT NOT NULL)")

    def add(self, agent: Agent) -> Agent:
        with open_database(self.database_file) as connection:
            connection.execute("INSERT INTO agents VALUES (?, ?, ?)",
                               (agent.agent_id, agent.status.value, agent.model_dump_json()))
        return agent

    def get(self, agent_id: str) -> Agent:
        with open_database(self.database_file) as connection:
            row = connection.execute("SELECT record_json FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(agent_id)
        return Agent.model_validate_json(row[0])

    def list(self) -> list[Agent]:
        with open_database(self.database_file) as connection:
            return [Agent.model_validate_json(row[0]) for row in connection.execute("SELECT record_json FROM agents")]

    def update(self, agent: Agent) -> None:
        with open_database(self.database_file) as connection:
            connection.execute("UPDATE agents SET status=?, record_json=? WHERE agent_id=?",
                               (agent.status.value, agent.model_dump_json(), agent.agent_id))
