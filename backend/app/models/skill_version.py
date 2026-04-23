from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .tool import Tool


class SkillVersion(SQLModel, table=True):
    __tablename__ = "skill_versions"

    id: int | None = Field(default=None, primary_key=True)
    skill_id: int = Field(foreign_key="skills.id", nullable=False)
    version: str = Field(nullable=False)
    status: str = Field(default="uploaded", nullable=False)
    upload_filename: str | None = Field(default=None)
    package_path: str | None = Field(default=None)
    extracted_path: str | None = Field(default=None)
    deployment_path: str | None = Field(default=None)
    manifest_data: dict = Field(default_factory=dict, sa_type=JSON)
    handler_config: dict = Field(default_factory=dict, sa_type=JSON)
    deployed_handler_config: dict = Field(default_factory=dict, sa_type=JSON)
    published_mcp_endpoint_url: str | None = Field(default=None)
    deploy_status: str = Field(default="not_deployed", nullable=False)
    deploy_error: str | None = Field(default=None)
    runtime_path: str | None = Field(default=None)
    venv_path: str | None = Field(default=None)
    dependency_manifest: dict = Field(default_factory=dict, sa_type=JSON)
    uploaded_by_user_id: int | None = Field(default=None, foreign_key="users.id", nullable=True)
    deployed_by_user_id: int | None = Field(default=None, foreign_key="users.id", nullable=True)
    deployed_at: datetime | None = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    tools: list["Tool"] = Relationship(back_populates="skill_version")
