from datetime import datetime
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlmodel import select

from app.core.permissions import can_access_workspace, can_manage_workspace, is_skill_visible_in_workspace
from app.dependencies import CurrentUserDep, SessionDep
from app.models import Skill, TeamMembership, Tool, Workspace
from app.services.skill_packages import (
    cleanup_skill_storage,
    clone_skill_storage,
    extract_package_archive,
    save_upload_to_disk,
    skill_storage_dir,
)

router = APIRouter()


class SkillCreate(BaseModel):
    name: str
    description: str | None = None
    handler_config: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    handler_config: dict[str, Any] | None = None


class SkillCopyRequest(BaseModel):
    target_workspace_id: int
    name: str | None = None


class SkillResponse(BaseModel):
    id: int
    workspace_id: int
    name: str
    description: str | None
    visibility: str
    enabled: bool
    handler_config: dict[str, Any]
    tools: list[dict[str, Any]]
    mcp_endpoint: str


class SkillAvailabilityItem(BaseModel):
    id: int
    name: str
    description: str | None
    enabled: bool
    tool_count: int


class SkillAvailabilityUpdate(BaseModel):
    enabled_skill_ids: list[int]


def _build_skill_response(skill: Skill) -> SkillResponse:
    tools = [
        {
            "id": tool.id,
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema or {},
        }
        for tool in skill.tools
    ]
    return SkillResponse(
        id=skill.id,
        workspace_id=skill.workspace_id,
        name=skill.name,
        description=skill.description,
        visibility=skill.visibility,
        enabled=skill.enabled,
        handler_config=skill.handler_config or {},
        tools=tools,
        mcp_endpoint=f"/mcp/{skill.workspace_id}/{skill.id}",
    )


def _replace_skill_tools(session, skill: Skill, tools: list[dict[str, Any]]) -> None:
    existing_tools = session.exec(select(Tool).where(Tool.skill_id == skill.id)).all()
    for tool in existing_tools:
        session.delete(tool)
    for tool_data in tools:
        tool = Tool(
            skill_id=skill.id,
            name=tool_data["name"],
            description=tool_data.get("description"),
            input_schema=tool_data.get("inputSchema") or tool_data.get("input_schema") or {},
        )
        session.add(tool)


def _merge_handler_config(current: dict[str, Any], incoming: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(current or {})
    if incoming:
        merged.update(incoming)
    return merged


def _require_workspace_admin(session, workspace_id: int, user_id: int) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if workspace.type == "personal":
        if workspace.owner_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return workspace
    membership = session.exec(
        select(TeamMembership).where(
            TeamMembership.team_id == workspace.team_id,
            TeamMembership.user_id == user_id,
        )
    ).first()
    if not membership or membership.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return workspace


def _get_workspace_membership(session, workspace: Workspace, user_id: int) -> TeamMembership | None:
    if workspace.type != "team" or workspace.team_id is None:
        return None
    return session.exec(
        select(TeamMembership).where(
            TeamMembership.team_id == workspace.team_id,
            TeamMembership.user_id == user_id,
        )
    ).first()


@router.post("/workspaces/{workspace_id}/skills", response_model=SkillResponse)
async def create_skill(workspace_id: int, data: SkillCreate, session: SessionDep, user: CurrentUserDep):
    if not await can_manage_workspace(user, workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    skill = Skill(
        workspace_id=workspace_id,
        name=data.name,
        description=data.description,
        visibility="private",
        handler_config=data.handler_config or {},
    )
    session.add(skill)
    session.commit()
    session.refresh(skill)

    if data.tools:
        _replace_skill_tools(session, skill, data.tools)
        session.commit()
        session.refresh(skill)

    return _build_skill_response(skill)


@router.post("/workspaces/{workspace_id}/skills/upload", response_model=SkillResponse)
async def upload_skill_package(
    workspace_id: int,
    session: SessionDep,
    user: CurrentUserDep,
    package: UploadFile = File(...),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    handler_config: str | None = Form(default=None),
):
    if not await can_manage_workspace(user, workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    if not package.filename or not package.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill package must be a .zip file")

    skill = Skill(
        workspace_id=workspace_id,
        name=name or Path(package.filename).stem,
        description=description,
        visibility="private",
        handler_config={},
    )
    session.add(skill)
    session.commit()
    session.refresh(skill)

    skill_dir = skill_storage_dir(skill.id)
    archive_path = skill_dir / "package.zip"
    extracted_dir = skill_dir / "package"

    try:
        await save_upload_to_disk(package, archive_path)
        package_data = extract_package_archive(archive_path, extracted_dir)
        manifest = package_data["manifest"]
        package_root = Path(package_data.get("root_dir") or extracted_dir)
        manifest_handler = manifest.get("handler") or {}
        form_handler = json.loads(handler_config) if handler_config else {}
        final_handler = _merge_handler_config(manifest_handler, form_handler)

        if final_handler.get("type") == "python_package":
            entrypoint = final_handler.get("entrypoint")
            if not entrypoint:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="python_package handler requires entrypoint in skill.json",
                )
            final_handler["package_dir"] = str(package_root)

        skill.name = name or manifest.get("name") or skill.name
        skill.description = description if description is not None else manifest.get("description")
        skill.visibility = "private"
        skill.handler_config = final_handler
        skill.updated_at = datetime.utcnow()
        session.add(skill)

        tools = manifest.get("tools") or []
        _replace_skill_tools(session, skill, tools)
        session.commit()
        session.refresh(skill)
        return _build_skill_response(skill)
    except Exception:
        session.delete(skill)
        session.commit()
        cleanup_skill_storage(skill.id)
        raise


@router.get("/workspaces/{workspace_id}/skills", response_model=list[SkillResponse])
async def list_skills(workspace_id: int, session: SessionDep, user: CurrentUserDep):
    if not await can_access_workspace(user, workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    workspace = session.get(Workspace, workspace_id)
    skills = session.exec(select(Skill).where(Skill.workspace_id == workspace_id)).all()
    membership = _get_workspace_membership(session, workspace, user.id) if workspace else None
    if workspace:
        skills = [skill for skill in skills if is_skill_visible_in_workspace(workspace, skill, membership)]
    return [_build_skill_response(skill) for skill in skills]


@router.get("/skills/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: int, session: SessionDep, user: CurrentUserDep):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not await can_access_workspace(user, skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    workspace = session.get(Workspace, skill.workspace_id)
    membership = _get_workspace_membership(session, workspace, user.id) if workspace else None
    if workspace and not is_skill_visible_in_workspace(workspace, skill, membership):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return _build_skill_response(skill)


@router.put("/skills/{skill_id}", response_model=SkillResponse)
async def update_skill(skill_id: int, data: SkillUpdate, session: SessionDep, user: CurrentUserDep):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not await can_manage_workspace(user, skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    update_data = data.model_dump(exclude_unset=True)
    if "handler_config" in update_data:
        skill.handler_config = _merge_handler_config(skill.handler_config or {}, update_data.pop("handler_config"))
    for key, value in update_data.items():
        setattr(skill, key, value)
    skill.updated_at = datetime.utcnow()

    session.add(skill)
    session.commit()
    session.refresh(skill)
    return _build_skill_response(skill)


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: int, session: SessionDep, user: CurrentUserDep):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not await can_manage_workspace(user, skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    session.delete(skill)
    session.commit()
    cleanup_skill_storage(skill_id)
    return {"ok": True}


def _rewrite_handler_paths(handler_config: dict[str, Any], rewrites: dict[str, str]) -> dict[str, Any]:
    payload = json.loads(json.dumps(handler_config or {}))
    for old, new in rewrites.items():
        if payload.get("package_dir") == old:
            payload["package_dir"] = new
        if payload.get("root_dir") == old:
            payload["root_dir"] = new
        plugins = payload.get("plugins")
        if isinstance(plugins, dict):
            for plugin in plugins.values():
                if isinstance(plugin, dict):
                    for key in ("source", "skill_doc"):
                        value = plugin.get(key)
                        if isinstance(value, str) and value.startswith(old):
                            plugin[key] = value.replace(old, new, 1)
    return payload


@router.post("/skills/{skill_id}/copy", response_model=SkillResponse)
async def copy_skill(skill_id: int, data: SkillCopyRequest, session: SessionDep, user: CurrentUserDep):
    source_skill = session.get(Skill, skill_id)
    if not source_skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not await can_access_workspace(user, source_skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if not await can_manage_workspace(user, data.target_workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required for target workspace")
    if data.target_workspace_id == source_skill.workspace_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source and target workspace must be different")

    copied = Skill(
        workspace_id=data.target_workspace_id,
        name=data.name or source_skill.name,
        description=source_skill.description,
        visibility="private",
        enabled=source_skill.enabled,
        handler_config=source_skill.handler_config or {},
    )
    session.add(copied)
    session.commit()
    session.refresh(copied)

    rewrites = clone_skill_storage(source_skill.id, copied.id)
    copied.handler_config = _rewrite_handler_paths(source_skill.handler_config or {}, rewrites)
    session.add(copied)

    for tool in source_skill.tools:
        session.add(
            Tool(
                skill_id=copied.id,
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema or {},
            )
        )

    session.commit()
    session.refresh(copied)
    return _build_skill_response(copied)


@router.get("/workspaces/{workspace_id}/skill-availability", response_model=list[SkillAvailabilityItem])
async def list_skill_availability(workspace_id: int, session: SessionDep, user: CurrentUserDep):
    _require_workspace_admin(session, workspace_id, user.id)
    skills = session.exec(select(Skill).where(Skill.workspace_id == workspace_id)).all()
    return [
        SkillAvailabilityItem(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            enabled=skill.enabled,
            tool_count=len(skill.tools),
        )
        for skill in skills
    ]


@router.put("/workspaces/{workspace_id}/skill-availability", response_model=list[SkillAvailabilityItem])
async def update_skill_availability(workspace_id: int, data: SkillAvailabilityUpdate, session: SessionDep, user: CurrentUserDep):
    _require_workspace_admin(session, workspace_id, user.id)
    skills = session.exec(select(Skill).where(Skill.workspace_id == workspace_id)).all()
    enabled_skill_ids = set(data.enabled_skill_ids)
    known_skill_ids = {skill.id for skill in skills if skill.id is not None}
    invalid = sorted(enabled_skill_ids - known_skill_ids)
    if invalid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown skills in workspace: {invalid}")

    for skill in skills:
        skill.enabled = skill.id in enabled_skill_ids
        skill.updated_at = datetime.utcnow()
        session.add(skill)
    session.commit()
    for skill in skills:
        session.refresh(skill)
    return [
        SkillAvailabilityItem(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            enabled=skill.enabled,
            tool_count=len(skill.tools),
        )
        for skill in skills
    ]
