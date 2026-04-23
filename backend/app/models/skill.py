from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import JSON
from sqlmodel import SQLModel, Field, Relationship

if TYPE_CHECKING:
    from .workspace import Workspace
    from .tool import Tool


class Skill(SQLModel, table=True):
    __tablename__ = "skills"

    id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspaces.id", nullable=False)
    name: str = Field(nullable=False)
    description: str | None = Field(default=None)
    visibility: str = Field(default="private", nullable=False)  # private or public
    enabled: bool = Field(default=True, nullable=False)
    handler_config: dict = Field(default_factory=dict, sa_type=JSON)
    current_approved_version_id: int | None = Field(default=None, foreign_key="skill_versions.id", nullable=True)
    deployed_version_id: int | None = Field(default=None, foreign_key="skill_versions.id", nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    workspace: "Workspace" = Relationship(back_populates="skills")
    tools: list["Tool"] = Relationship(back_populates="skill")
