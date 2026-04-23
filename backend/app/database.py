from datetime import datetime

from sqlalchemy import inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.config import settings
from app.models import Skill, SkillVersion, Tool, Workspace, WorkspaceSkillExposure


def build_engine():
    if settings.database_url == "sqlite://":
        return create_engine(
            settings.database_url,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    if settings.database_url.startswith("sqlite"):
        return create_engine(
            settings.database_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return create_engine(settings.database_url, echo=False)


engine = build_engine()


def _ensure_column(table_name: str, column_name: str, column_sql: str) -> None:
    with engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
        if column_name not in columns:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"))


def _build_legacy_manifest(skill: Skill, tools: list[Tool]) -> dict:
    return {
        "name": skill.name,
        "description": skill.description,
        "visibility": skill.visibility,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema or {},
            }
            for tool in tools
        ],
    }


def _legacy_version_label(skill: Skill) -> str:
    handler = skill.handler_config or {}
    version = handler.get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return "legacy-v1"


def _ensure_workspace_exposure(
    session: Session,
    workspace_id: int,
    skill_id: int,
    enabled: bool,
) -> bool:
    exposure = session.get(WorkspaceSkillExposure, (workspace_id, skill_id))
    if exposure is None:
        exposure = WorkspaceSkillExposure(
            workspace_id=workspace_id,
            skill_id=skill_id,
            enabled=enabled,
            updated_at=datetime.utcnow(),
        )
        session.add(exposure)
        return True
    if exposure.enabled != enabled:
        exposure.enabled = enabled
        exposure.updated_at = datetime.utcnow()
        session.add(exposure)
        return True
    return False


def _migrate_legacy_skill_data() -> None:
    with Session(engine) as session:
        workspace_map = {workspace.id: workspace for workspace in session.exec(select(Workspace)).all()}
        skills = session.exec(select(Skill)).all()
        changed = False

        for skill in skills:
            tools = session.exec(select(Tool).where(Tool.skill_id == skill.id)).all()
            versions = session.exec(select(SkillVersion).where(SkillVersion.skill_id == skill.id)).all()
            workspace = workspace_map.get(skill.workspace_id)

            if not versions:
                legacy_version = SkillVersion(
                    skill_id=skill.id,
                    version=_legacy_version_label(skill),
                    status="approved",
                    upload_filename="legacy-import.zip",
                    package_path=None,
                    extracted_path=None,
                    manifest_data=_build_legacy_manifest(skill, tools),
                    handler_config=skill.handler_config or {},
                    uploaded_by_user_id=workspace.owner_id if workspace else None,
                    created_at=skill.created_at,
                    updated_at=skill.updated_at,
                )
                session.add(legacy_version)
                session.flush()
                versions = [legacy_version]
                skill.current_approved_version_id = legacy_version.id
                session.add(skill)
                changed = True

            approved_version = next((version for version in versions if version.status == "approved"), None)
            if skill.current_approved_version_id is None and approved_version is not None:
                skill.current_approved_version_id = approved_version.id
                session.add(skill)
                changed = True
            if skill.deployed_version_id is None and approved_version is not None:
                skill.deployed_version_id = approved_version.id
                session.add(skill)
                changed = True

            default_version_id = skill.current_approved_version_id or approved_version.id if approved_version else None
            if default_version_id is not None:
                for tool in tools:
                    if tool.skill_version_id is None:
                        tool.skill_version_id = default_version_id
                        session.add(tool)
                        changed = True

            if workspace and workspace.type == "team":
                changed = _ensure_workspace_exposure(session, workspace.id, skill.id, bool(skill.enabled)) or changed

        if changed:
            session.commit()


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _ensure_column("skills", "current_approved_version_id", "INTEGER")
    _ensure_column("skills", "deployed_version_id", "INTEGER")
    _ensure_column("tools", "skill_version_id", "INTEGER")
    _ensure_column("skill_versions", "deployment_path", "TEXT")
    _ensure_column("skill_versions", "deployed_handler_config", "JSON")
    _ensure_column("skill_versions", "published_mcp_endpoint_url", "TEXT")
    _ensure_column("skill_versions", "deploy_status", "TEXT DEFAULT 'not_deployed' NOT NULL")
    _ensure_column("skill_versions", "deploy_error", "TEXT")
    _ensure_column("skill_versions", "runtime_path", "TEXT")
    _ensure_column("skill_versions", "venv_path", "TEXT")
    _ensure_column("skill_versions", "dependency_manifest", "JSON")
    _ensure_column("skill_versions", "deployed_by_user_id", "INTEGER")
    _ensure_column("skill_versions", "deployed_at", "DATETIME")
    _migrate_legacy_skill_data()


def get_session() -> Session:
    with Session(engine) as session:
        yield session
