from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import JSON
from sqlmodel import SQLModel, Field, Relationship

if TYPE_CHECKING:
    from .user import User
    from .workspace import Workspace


class Team(SQLModel, table=True):
    __tablename__ = "teams"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    owner_id: int = Field(foreign_key="users.id", nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    owner: "User" = Relationship()
    memberships: list["TeamMembership"] = Relationship(back_populates="team")
    workspaces: list["Workspace"] = Relationship(back_populates="team")


class TeamMembership(SQLModel, table=True):
    __tablename__ = "team_memberships"

    team_id: int = Field(foreign_key="teams.id", primary_key=True)
    user_id: int = Field(foreign_key="users.id", primary_key=True)
    role: str = Field(default="member", nullable=False)  # admin or member
    skill_preferences: dict = Field(default_factory=dict, sa_type=JSON)
    skill_preferences_configured: bool = Field(default=False, nullable=False)

    team: "Team" = Relationship(back_populates="memberships")
    user: "User" = Relationship(back_populates="team_memberships")


class TeamJoinRequest(SQLModel, table=True):
    __tablename__ = "team_join_requests"

    id: int | None = Field(default=None, primary_key=True)
    team_id: int = Field(foreign_key="teams.id", nullable=False)
    user_id: int = Field(foreign_key="users.id", nullable=False)
    status: str = Field(default="pending", nullable=False)  # pending approved rejected
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
