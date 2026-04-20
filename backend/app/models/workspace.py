from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlmodel import SQLModel, Field, Relationship

if TYPE_CHECKING:
    from .user import User
    from .team import Team
    from .skill import Skill


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    type: str = Field(default="personal", nullable=False)  # personal or team
    owner_id: int = Field(foreign_key="users.id", nullable=False)
    team_id: int | None = Field(default=None, foreign_key="teams.id", nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    owner: "User" = Relationship(back_populates="workspaces")
    team: Optional["Team"] = Relationship(back_populates="workspaces")
    skills: list["Skill"] = Relationship(back_populates="workspace")
