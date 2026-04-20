from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import JSON
from sqlmodel import SQLModel, Field, Relationship

if TYPE_CHECKING:
    from .skill import Skill


class Tool(SQLModel, table=True):
    __tablename__ = "tools"

    id: int | None = Field(default=None, primary_key=True)
    skill_id: int = Field(foreign_key="skills.id", nullable=False)
    name: str = Field(nullable=False)
    description: str | None = Field(default=None)
    input_schema: dict = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    skill: "Skill" = Relationship(back_populates="tools")
