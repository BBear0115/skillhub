from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field, Relationship

if TYPE_CHECKING:
    from .user import User
    from .workspace import Workspace


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id", name="uq_api_keys_user_workspace"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", nullable=False)
    workspace_id: int = Field(foreign_key="workspaces.id", nullable=False)
    key_hash: str = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    user: "User" = Relationship(back_populates="api_keys")
    workspace: "Workspace" = Relationship()
