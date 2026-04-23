from datetime import datetime

from sqlmodel import Field, SQLModel


class WorkspaceSkillExposure(SQLModel, table=True):
    __tablename__ = "workspace_skill_exposures"

    workspace_id: int = Field(foreign_key="workspaces.id", primary_key=True)
    skill_id: int = Field(foreign_key="skills.id", primary_key=True)
    enabled: bool = Field(default=False, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
