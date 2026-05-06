from datetime import datetime
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import select

from app.core.permissions import (
    can_access_workspace,
    can_install_to_workspace,
    can_manage_workspace,
    can_review_skill,
    current_runtime_version,
    is_super_admin_user,
    team_member_skill_enabled,
    workspace_skill_exposure_enabled,
)
from app.dependencies import CurrentUserDep, SessionDep
from app.models import Skill, SkillVersion, TeamMembership, Tool, Workspace, WorkspaceSkillExposure
from app.services.skill_packages import (
    cleanup_review_workbench,
    cleanup_skill_version_storage,
    cleanup_deployed_skill,
    ensure_storage_root,
    extract_package_archive,
    prepare_review_workbench,
    remove_tree,
    save_upload_to_disk,
    skill_version_storage_dir,
)
from app.services.global_transfer_tools import delete_artifact, list_global_tool_definitions, read_artifact_manifest, store_existing_file_as_artifact
from app.services.skill_runtime import SkillDeploymentError, deploy_skill_runtime, ensure_admin_workspace

router = APIRouter()
logger = logging.getLogger(__name__)


class ToolSummary(BaseModel):
    id: int
    name: str
    description: str | None
    input_schema: dict[str, Any]


class SkillVersionSummary(BaseModel):
    id: int
    version: str
    status: str
    deployed: bool = False


class SkillVersionResponse(BaseModel):
    id: int
    skill_id: int
    version: str
    status: str
    upload_filename: str | None
    uploaded_by_user_id: int | None
    uploaded_by_account: str | None
    package_download_url: str
    tools: list[ToolSummary]
    is_current_approved: bool
    is_current_deployed: bool
    deployment_path: str | None
    deploy_status: str
    deploy_error: str | None
    runtime_path: str | None
    venv_path: str | None
    dependency_manifest: dict[str, Any]
    deployed_at: str | None
    deployed_by_user_id: int | None
    deployed_by_account: str | None
    published_mcp_endpoint_url: str | None
    created_at: str
    updated_at: str


class SkillResponse(BaseModel):
    id: int
    workspace_id: int
    name: str
    description: str | None
    visibility: str
    mcp_endpoint: str
    current_approved_version_id: int | None
    current_approved_version: SkillVersionSummary | None
    deployed_version_id: int | None
    deployed_version: SkillVersionSummary | None
    mcp_ready: bool
    prompt_content: str | None = None
    prompt_join_logic: str | None = None
    agent_prompt: str | None = None
    version_count: int
    exposed_to_workspace: bool | None = None
    created_at: str
    updated_at: str


class ReviewSkillVersionResponse(SkillVersionResponse):
    workspace_id: int
    workspace_name: str
    skill_name: str


class ReviewWorkbenchResponse(BaseModel):
    review_attempt_id: str
    version: ReviewSkillVersionResponse
    workbench_path: str
    workbench_package_path: str | None
    workbench_extracted_path: str | None
    deployment_kind: str
    deployment_entrypoint: str | None = None
    deployment_ready: bool
    tool_count: int
    manifest_data: dict[str, Any]
    handler_config: dict[str, Any]
    deployment_steps: list[str]


class SkillExposureItem(BaseModel):
    skill_id: int
    name: str
    description: str | None
    enabled: bool
    current_approved_version_id: int
    current_approved_version: str


class SkillExposureUpdate(BaseModel):
    enabled_skill_ids: list[int]


class SkillVisibilityUpdate(BaseModel):
    visibility: str


class SkillSyncRequest(BaseModel):
    target_workspace_id: int
    visibility: str | None = None


class SkillDeployRequest(BaseModel):
    mcp_endpoint_url: str | None = None
    review_attempt_id: str | None = None


class WorkspacePromptResponse(BaseModel):
    workspace_id: int
    workspace_name: str
    workspace_mcp_url: str
    prompt_text: str
    available_skills: list[dict[str, Any]]
    global_tools: list[dict[str, Any]]


class ArtifactUploadResponse(BaseModel):
    artifact_id: str
    filename: str
    kind: str
    content_path: str
    download_url: str


class SkillPromptConfigUpdate(BaseModel):
    prompt_content: str
    prompt_join_logic: str


class MarketSkillResponse(SkillResponse):
    uploader_account: str | None
    latest_version: SkillVersionResponse | None
    tools: list[ToolSummary]
    prompt_content: str
    prompt_join_logic: str
    agent_prompt: str | None


def _workspace_membership(session, workspace: Workspace, user_id: int) -> TeamMembership | None:
    if workspace.type != "team" or workspace.team_id is None:
        return None
    return session.exec(
        select(TeamMembership).where(
            TeamMembership.team_id == workspace.team_id,
            TeamMembership.user_id == user_id,
        )
    ).first()


def _available_skills_for_prompt(session, workspace: Workspace, membership: TeamMembership | None) -> list[tuple[Skill, SkillVersion]]:
    local_skills = session.exec(select(Skill).where(Skill.workspace_id == workspace.id)).all()
    public_skills = session.exec(select(Skill).where(Skill.visibility == "public")).all()
    visible: dict[int, tuple[Skill, SkillVersion]] = {}
    for skill in local_skills:
        version = current_runtime_version(session, skill)
        if version is None:
            continue
        if workspace.type == "team" and skill.visibility != "public":
            if not workspace_skill_exposure_enabled(session, workspace, skill.id):
                continue
            if not team_member_skill_enabled(membership, skill.id):
                continue
        visible[skill.id] = (skill, version)
    for skill in public_skills:
        version = current_runtime_version(session, skill)
        if version is None:
            continue
        if workspace.type == "team" and not team_member_skill_enabled(membership, skill.id):
            continue
        visible[skill.id] = (skill, version)
    return sorted(visible.values(), key=lambda item: (item[0].name.lower(), item[0].id))


def _require_team_admin(session, workspace_id: int, user_id: int) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if workspace.type != "team":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Team workspace required")
    membership = _workspace_membership(session, workspace, user_id)
    if not membership or membership.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return workspace


def _version_tools(session, version_id: int) -> list[Tool]:
    return session.exec(select(Tool).where(Tool.skill_version_id == version_id)).all()


def _tool_summary(tool: Tool) -> ToolSummary:
    return ToolSummary(
        id=tool.id,
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema or {},
    )


def _version_summary(version: SkillVersion | None) -> SkillVersionSummary | None:
    if version is None:
        return None
    return SkillVersionSummary(
        id=version.id,
        version=version.version,
        status=version.status,
        deployed=bool(version.deployment_path),
    )


def _build_version_response(session, version: SkillVersion, skill: Skill | None = None) -> SkillVersionResponse:
    tools = _version_tools(session, version.id)
    uploader_account = None
    deployed_by_account = None
    if version.uploaded_by_user_id is not None:
        from app.models import User

        uploader = session.get(User, version.uploaded_by_user_id)
        uploader_account = uploader.account if uploader else None
    if version.deployed_by_user_id is not None:
        from app.models import User

        deployed_by = session.get(User, version.deployed_by_user_id)
        deployed_by_account = deployed_by.account if deployed_by else None
    if skill is None:
        skill = session.get(Skill, version.skill_id)
    return SkillVersionResponse(
        id=version.id,
        skill_id=version.skill_id,
        version=version.version,
        status=version.status,
        upload_filename=version.upload_filename,
        uploaded_by_user_id=version.uploaded_by_user_id,
        uploaded_by_account=uploader_account,
        package_download_url=f"/skill-versions/{version.id}/download",
        tools=[_tool_summary(tool) for tool in tools],
        is_current_approved=bool(skill and skill.current_approved_version_id == version.id),
        is_current_deployed=bool(skill and skill.deployed_version_id == version.id),
        deployment_path=version.deployment_path,
        deploy_status=version.deploy_status,
        deploy_error=version.deploy_error,
        runtime_path=version.runtime_path,
        venv_path=version.venv_path,
        dependency_manifest=version.dependency_manifest or {},
        deployed_at=version.deployed_at.isoformat() if version.deployed_at else None,
        deployed_by_user_id=version.deployed_by_user_id,
        deployed_by_account=deployed_by_account,
        published_mcp_endpoint_url=version.published_mcp_endpoint_url,
        created_at=version.created_at.isoformat(),
        updated_at=version.updated_at.isoformat(),
    )


def _bearer_token_from_authorization(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _build_skill_response(
    session,
    skill: Skill,
    workspace: Workspace | None = None,
    access_token: str | None = None,
    base_url: str | None = None,
) -> SkillResponse:
    approved_version = session.get(SkillVersion, skill.current_approved_version_id) if skill.current_approved_version_id else None
    deployed_version = session.get(SkillVersion, skill.deployed_version_id) if skill.deployed_version_id else None
    runtime_version = current_runtime_version(session, skill)
    version_count = session.exec(select(SkillVersion).where(SkillVersion.skill_id == skill.id)).all()
    if workspace is None:
        workspace = session.get(Workspace, skill.workspace_id)
    exposed = None
    if workspace and workspace.type == "team":
        exposed = workspace_skill_exposure_enabled(session, workspace, skill.id)
    runtime_ready = runtime_version is not None
    return SkillResponse(
        id=skill.id,
        workspace_id=skill.workspace_id,
        name=skill.name,
        description=skill.description,
        visibility=skill.visibility,
        mcp_endpoint=f"/mcp/{skill.workspace_id}/{skill.id}",
        current_approved_version_id=skill.current_approved_version_id,
        current_approved_version=_version_summary(approved_version),
        deployed_version_id=skill.deployed_version_id,
        deployed_version=_version_summary(deployed_version),
        mcp_ready=runtime_ready,
        prompt_content=_prompt_content(skill, runtime_version or deployed_version or approved_version),
        prompt_join_logic=_prompt_join_logic(skill, runtime_version or deployed_version or approved_version),
        agent_prompt=_agent_prompt(session, skill, runtime_version, access_token, base_url),
        version_count=len(version_count),
        exposed_to_workspace=exposed,
        created_at=skill.created_at.isoformat(),
        updated_at=skill.updated_at.isoformat(),
    )


def _prompt_content(skill: Skill, version: SkillVersion | None = None) -> str:
    handler = dict(skill.handler_config or {})
    if version is not None:
        handler.update(version.handler_config or {})
        handler.update(version.deployed_handler_config or {})
    content = handler.get("prompt_content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    doc_path = handler.get("doc_path")
    if isinstance(doc_path, str) and Path(doc_path).exists():
        try:
            return Path(doc_path).read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return skill.description or ""


def _prompt_join_logic(skill: Skill, version: SkillVersion | None = None) -> str:
    handler = dict(skill.handler_config or {})
    if version is not None:
        handler.update(version.handler_config or {})
        handler.update(version.deployed_handler_config or {})
    logic = handler.get("prompt_join_logic")
    if isinstance(logic, str) and logic.strip():
        return logic.strip()
    return _mcp_call_prompt_block()


def _mcp_call_prompt_block() -> str:
    return "\n".join(
        [
            "Use the endpoint as a concrete MCP server, not as a plain REST endpoint.",
            "1. Send a JSON-RPC initialize request to the MCP endpoint with the Authorization header.",
            "2. Read the Mcp-Session-Id response header and include it on every later MCP request.",
            "3. Call tools/list before selecting a business tool. The response contains the callable tool names and input schemas.",
            "4. Call resources/list and resources/read when resources are present. Use the returned Skill instructions before tools/call.",
            "5. Call tools/call with params.name set to the selected tool and params.arguments set to a JSON object matching that tool schema.",
            "6. For file or audio work, upload exactly one input artifact first, call the business tool with that artifact id, download the output, then delete input and output artifacts.",
        ]
    )


def _global_transfer_prompt_block() -> str:
    return "\n".join(
        [
            "Global upload, output, and cleanup tools:",
            "- global_upload_audio_files: upload exactly one audio file per call. Use for .wav, .mp3, .flac, .m4a, or .ogg content when using MCP-only upload.",
            "- global_upload_text_files: upload exactly one text file per call. Use for .txt, .csv, .json, logs, or other text inputs when using MCP-only upload.",
            "- global_download_processed_artifacts: fetch metadata and download_url for exactly one processed or uploaded artifact per call.",
            "- global_download_processed_artifacts_and_cleanup: return one or more processed artifacts as a base64 zip payload and then delete those artifacts from server storage.",
            "- global_delete_uploaded_artifacts: delete exactly one uploaded or processed artifact per call. Use mode=soft unless the user explicitly asks for hard deletion.",
            "After every successful business skill call, automatically download the processed artifact first, then delete the original input artifact and the processed artifact so server storage is not occupied.",
            "A server-side safety cleanup also hard-deletes remaining uploaded audio and processed audio/archive artifacts every day at 02:00 server time.",
            "",
            "HTTP artifact APIs available with the same Authorization header:",
            "- POST /artifacts/audio with multipart field file uploads exactly one local audio file and returns artifact_id plus download_url.",
            "- GET /artifacts/{artifact_id}/download downloads exactly one artifact.",
            "- DELETE /artifacts/{artifact_id}?mode=soft deletes exactly one artifact.",
            "",
            "Streaming workflow required for multiple files:",
            "1. Upload one audio or text file.",
            "2. Call the business MCP tool with exactly one artifact id when the tool accepts artifacts.",
            "3. Read produced_artifacts, processed_archive_artifact_id, or download_url from the tool result.",
            "4. Download processed artifacts, using global_download_processed_artifacts_and_cleanup when a single skill call produced multiple outputs.",
            "5. Delete exactly one original artifact, and delete the processed artifact too if the user wants cleanup.",
            "6. Continue with the next file only after the current file has completed upload -> process -> download -> delete.",
            "Do not batch uploads or business processing. Bulk cleanup is allowed only with global_download_processed_artifacts_and_cleanup after outputs have been produced.",
            "",
            "Required MCP argument shapes:",
            "- global_upload_audio_files: {\"files\":[{\"filename\":\"input.wav\",\"mime_type\":\"audio/wav\",\"content_base64\":\"<base64>\"}]}",
            "- global_upload_text_files: {\"files\":[{\"filename\":\"input.txt\",\"content_text\":\"<text>\",\"encoding\":\"utf-8\"}]}",
            "- global_download_processed_artifacts: {\"artifact_ids\":[\"<artifact_id>\"],\"include_inline_text\":true}",
            "- global_download_processed_artifacts_and_cleanup: {\"artifact_ids\":[\"<artifact_id>\"],\"cleanup_mode\":\"hard\"}",
            "- global_delete_uploaded_artifacts: {\"artifact_ids\":[\"<artifact_id>\"],\"mode\":\"soft\"}",
        ]
    )


def _uses_global_transfer_helpers(skill: Skill, tools: list[Tool]) -> bool:
    for tool in tools:
        if tool.name == "DNSMOS Audio Filter":
            return True
        schema_text = json.dumps(tool.input_schema or {}, ensure_ascii=False).lower()
        if "input_artifact_ids" in schema_text or "artifact_id" in schema_text:
            return True
    handler_text = json.dumps(skill.handler_config or {}, ensure_ascii=False).lower()
    return "input_artifact_ids" in handler_text or "produced_artifacts" in handler_text


def _agent_prompt(
    session,
    skill: Skill,
    version: SkillVersion | None,
    access_token: str | None = None,
    base_url: str | None = None,
) -> str | None:
    if version is None or not version.published_mcp_endpoint_url:
        return None
    endpoint = version.published_mcp_endpoint_url
    tools = [tool.name for tool in _version_tools(session, version.id)] if version is not None and version.id else []
    return "\n".join(
        [
            "You can use the following SkillHub MCP skill.",
            "",
            f"Skill: {skill.name}",
            f"Version: {version.version if version else '-'}",
            f"MCP endpoint: {endpoint}",
            f"Tools: {', '.join(tools) if tools else 'No declared tools'}",
            "Global MCP helper tools are available on the same endpoint.",
            "",
            _global_transfer_prompt_block(),
            "",
            "Usage instructions:",
            _prompt_content(skill, version),
            "",
            "Connection and call logic:",
            _prompt_join_logic(skill, version),
            "",
            "Authentication:",
            f"Use this exact header when connecting: Authorization: Bearer {access_token}" if access_token else "Use Authorization: Bearer <access_token> when connecting.",
            "Do not omit the Authorization header. The MCP endpoint returns 401 Authentication required without it.",
        ]
    )


def _build_market_skill_response(session, skill: Skill, access_token: str | None = None, base_url: str | None = None) -> MarketSkillResponse:
    workspace = session.get(Workspace, skill.workspace_id)
    latest_version = session.exec(
        select(SkillVersion).where(SkillVersion.skill_id == skill.id).order_by(SkillVersion.created_at.desc())
    ).first()
    runtime_version = current_runtime_version(session, skill)
    uploader_account = None
    if latest_version and latest_version.uploaded_by_user_id is not None:
        from app.models import User

        uploader = session.get(User, latest_version.uploaded_by_user_id)
        uploader_account = uploader.account if uploader else None
    version_for_tools = runtime_version or latest_version
    base = _build_skill_response(session, skill, workspace, access_token, base_url).model_dump(
        exclude={"prompt_content", "prompt_join_logic", "agent_prompt"}
    )
    return MarketSkillResponse(
        **base,
        uploader_account=uploader_account,
        latest_version=_build_version_response(session, latest_version, skill) if latest_version else None,
        tools=[_tool_summary(tool) for tool in _version_tools(session, version_for_tools.id)] if version_for_tools else [],
        prompt_content=_prompt_content(skill, runtime_version or latest_version),
        prompt_join_logic=_prompt_join_logic(skill, runtime_version or latest_version),
        agent_prompt=_agent_prompt(session, skill, runtime_version, access_token, base_url),
    )


def _build_review_version_response(
    session,
    version: SkillVersion,
    skill: Skill,
    workspace: Workspace,
) -> ReviewSkillVersionResponse:
    base = _build_version_response(session, version, skill)
    return ReviewSkillVersionResponse(
        **base.model_dump(),
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        skill_name=skill.name,
    )


def _resolve_deploy_source(version: SkillVersion) -> Path:
    review_root = ensure_storage_root() / "review-workbenches" / f"version-{version.id}"
    if review_root.exists():
        attempts = sorted((item for item in review_root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True)
        for attempt in attempts:
            extracted = attempt / "package"
            if extracted.exists():
                return extracted.resolve()
    if version.extracted_path and Path(version.extracted_path).exists():
        return Path(version.extracted_path).resolve()
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill version has no deployable extracted package")


def _merge_handler_config(current: dict[str, Any], incoming: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(current or {})
    if incoming:
        merged.update(incoming)
    return merged


def _validate_visibility(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in {"private", "public"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="visibility must be private or public")
    return normalized


def _runtime_handler_for_version(version: SkillVersion) -> dict[str, Any]:
    return version.deployed_handler_config or version.handler_config or {}


def _rewrite_path_value(value: Any, source_root: Path, target_root: Path) -> Any:
    if isinstance(value, str):
        try:
            source_path = Path(value).resolve()
            relative_path = source_path.relative_to(source_root.resolve())
            return str((target_root / relative_path).resolve())
        except (OSError, ValueError):
            return value
        return value
    if isinstance(value, list):
        return [_rewrite_path_value(item, source_root, target_root) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_path_value(item, source_root, target_root) for key, item in value.items()}
    return value


def _build_deployed_handler_config(version: SkillVersion, deployed_package_path: Path) -> dict[str, Any]:
    source_root = Path(version.extracted_path or "").resolve() if version.extracted_path else None
    deployed_root = deployed_package_path.resolve()
    base_handler = version.handler_config or {}
    if source_root is None:
        return dict(base_handler)
    rewritten = _rewrite_path_value(base_handler, source_root, deployed_root)
    if isinstance(rewritten, dict):
        return rewritten
    return dict(base_handler)


def _validate_mcp_endpoint_url(value: str) -> str:
    url = value.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mcp_endpoint_url must be a complete http(s) URL")
    return url


def _create_version_tools(session, skill: Skill, version: SkillVersion, tools: list[dict[str, Any]]) -> None:
    for tool_data in tools:
        session.add(
            Tool(
                skill_id=skill.id,
                skill_version_id=version.id,
                name=tool_data["name"],
                description=tool_data.get("description"),
                input_schema=tool_data.get("inputSchema") or tool_data.get("input_schema") or {},
            )
        )


def _ensure_team_exposure_row(session, workspace: Workspace, skill: Skill) -> None:
    if workspace.type != "team":
        return
    exposure = session.get(WorkspaceSkillExposure, (workspace.id, skill.id))
    if exposure is None:
        exposure = WorkspaceSkillExposure(workspace_id=workspace.id, skill_id=skill.id, enabled=False)
        session.add(exposure)


def _skill_versions(session, skill_id: int) -> list[SkillVersion]:
    return session.exec(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.created_at.desc(), SkillVersion.id.desc())
    ).all()


def _require_skill_access(session, skill_id: int, user) -> tuple[Skill, Workspace]:
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    workspace = session.get(Workspace, skill.workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if not is_super_admin_user(user) and not (workspace.type == "personal" and workspace.owner_id == user.id):
        if workspace.type == "team":
            membership = _workspace_membership(session, workspace, user.id)
            if not membership:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        elif workspace.owner_id != user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return skill, workspace


@router.post("/workspaces/{workspace_id}/skills", response_model=SkillResponse)
async def create_skill(workspace_id: int, session: SessionDep, user: CurrentUserDep):
    if not await can_install_to_workspace(user, workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace access required")
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Inline skill creation has been removed. Upload a ZIP package instead.",
    )


@router.post("/workspaces/{workspace_id}/skills/upload", response_model=SkillResponse)
async def upload_skill_package(
    workspace_id: int,
    session: SessionDep,
    user: CurrentUserDep,
    package: UploadFile = File(...),
    name: str | None = Form(default=None),
    version: str | None = Form(default=None),
    description: str | None = Form(default=None),
    visibility: str | None = Form(default=None),
    handler_config: str | None = Form(default=None),
):
    if not await can_install_to_workspace(user, workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace access required")
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if not package.filename or not package.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill package must be a .zip file")

    created_skill = False
    skill: Skill | None = None
    version_row: SkillVersion | None = None

    try:
        form_handler = json.loads(handler_config) if handler_config else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="handler_config must be valid JSON") from exc

    try:
        requested_visibility = _validate_visibility(visibility)
        staging_parent = ensure_storage_root() / "tmp" / "upload-staging"
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging_root_path = staging_parent / f"skillhub-upload-{uuid4().hex}"
        remove_tree(staging_root_path)
        staging_root_path.mkdir(parents=True, exist_ok=True)
        try:
            staged_archive_path = staging_root_path / "package.zip"
            staged_extract_dir = staging_root_path / "package"

            await save_upload_to_disk(package, staged_archive_path)
            package_data = extract_package_archive(staged_archive_path, staged_extract_dir)
            manifest = package_data["manifest"]
            manifest_name = manifest.get("name")
            manifest_version = manifest.get("version")
            requested_version = version.strip() if isinstance(version, str) and version.strip() else None
            resolved_version = requested_version or (manifest_version.strip() if isinstance(manifest_version, str) and manifest_version.strip() else None)
            if not isinstance(manifest_name, str) or not manifest_name.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill manifest must contain a name")
            if not resolved_version:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill version is required")

            skill_name = name or manifest_name.strip()
            skill = session.exec(select(Skill).where(Skill.workspace_id == workspace_id, Skill.name == skill_name)).first()
            if skill is None:
                skill = Skill(
                    workspace_id=workspace_id,
                    name=skill_name,
                    description=description if description is not None else manifest.get("description"),
                    visibility=requested_visibility or _validate_visibility(manifest.get("visibility")) or "private",
                    handler_config={},
                )
                session.add(skill)
                session.commit()
                session.refresh(skill)
                created_skill = True

            duplicate_version = session.exec(
                select(SkillVersion).where(
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.version == resolved_version,
                )
            ).first()
            if duplicate_version:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Version already exists: {resolved_version}")

            version_row = SkillVersion(
                skill_id=skill.id,
                version="pending",
                status="uploaded",
                upload_filename=package.filename,
                package_path=None,
                extracted_path=None,
                manifest_data={},
                handler_config={},
                uploaded_by_user_id=user.id,
            )
            session.add(version_row)
            session.commit()
            session.refresh(version_row)

            version_dir = skill_version_storage_dir(version_row.id)
            remove_tree(version_dir)
            archive_path = version_dir / "package.zip"
            extracted_dir = version_dir / "package"
            version_dir.mkdir(parents=True, exist_ok=True)
            package_root_in_staging = Path(package_data.get("root_dir") or staged_extract_dir)
            package_root_relative = package_root_in_staging.resolve().relative_to(staged_extract_dir.resolve())

            shutil.copy2(staged_archive_path, archive_path)
            shutil.copytree(staged_extract_dir, extracted_dir)
            package_root = (extracted_dir / package_root_relative).resolve()
        finally:
            remove_tree(staging_root_path)

        manifest_handler = manifest.get("handler") or {}
        final_handler = _merge_handler_config(manifest_handler, form_handler)
        rewritten_handler = _rewrite_path_value(final_handler, package_root_in_staging.resolve(), package_root)
        final_handler = rewritten_handler if isinstance(rewritten_handler, dict) else final_handler
        if final_handler.get("type") == "python_package":
            entrypoint = final_handler.get("entrypoint")
            if not entrypoint:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="python_package handler requires entrypoint in skill.json",
                )
            final_handler["package_dir"] = str(package_root)
        skill_doc_path = package_root / "SKILL.md"
        if skill_doc_path.exists():
            final_handler["doc_path"] = str(skill_doc_path)

        if skill is None or version_row is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Skill upload state was not initialized")

        skill.name = name or manifest.get("name") or skill.name
        skill.description = description if description is not None else manifest.get("description") or skill.description
        skill.visibility = requested_visibility or _validate_visibility(manifest.get("visibility")) or skill.visibility
        skill.updated_at = datetime.utcnow()
        version_row.version = resolved_version
        version_row.status = "uploaded"
        version_row.package_path = str(archive_path)
        version_row.extracted_path = str(package_root)
        version_row.manifest_data = manifest
        version_row.handler_config = final_handler
        version_row.updated_at = datetime.utcnow()
        session.add(skill)
        session.add(version_row)
        _create_version_tools(session, skill, version_row, manifest.get("tools") or [])
        _ensure_team_exposure_row(session, workspace, skill)
        session.commit()
        session.refresh(skill)
        return _build_skill_response(session, skill, workspace)
    except Exception:
        logger.exception(
            "Skill upload failed: workspace_id=%s skill_id=%s version_id=%s",
            workspace_id,
            skill.id if skill else None,
            version_row.id if version_row else None,
        )
        session.rollback()
        persisted_version = session.get(SkillVersion, version_row.id) if version_row else None
        if persisted_version:
            session.delete(persisted_version)
        if created_skill and skill:
            persisted_skill = session.get(Skill, skill.id)
            if persisted_skill:
                session.delete(persisted_skill)
        session.commit()
        if version_row:
            cleanup_skill_version_storage(version_row.id)
        raise


@router.get("/workspaces/{workspace_id}/skills", response_model=list[SkillResponse])
async def list_skills(
    workspace_id: int,
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
    authorization: str | None = Header(default=None),
):
    if not await can_access_workspace(user, workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    skills = session.exec(select(Skill).where(Skill.workspace_id == workspace_id)).all()
    access_token = _bearer_token_from_authorization(authorization)
    base_url = str(request.base_url).rstrip("/")
    return [_build_skill_response(session, skill, workspace, access_token, base_url) for skill in skills]


@router.get("/market/skills", response_model=list[MarketSkillResponse])
async def list_market_skills(session: SessionDep, user: CurrentUserDep, request: Request, authorization: str | None = Header(default=None)):
    skills = session.exec(select(Skill).where(Skill.visibility == "public")).all()
    access_token = _bearer_token_from_authorization(authorization)
    base_url = str(request.base_url).rstrip("/")
    return [_build_market_skill_response(session, skill, access_token, base_url) for skill in skills]


@router.get("/market/skills/{skill_id}", response_model=MarketSkillResponse)
async def get_market_skill(skill_id: int, request: Request, session: SessionDep, user: CurrentUserDep, authorization: str | None = Header(default=None)):
    skill = session.get(Skill, skill_id)
    if not skill or skill.visibility != "public":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market skill not found")
    return _build_market_skill_response(session, skill, _bearer_token_from_authorization(authorization), str(request.base_url).rstrip("/"))


@router.delete("/market/skills/{skill_id}")
async def delete_market_skill(skill_id: int, session: SessionDep, user: CurrentUserDep):
    if not is_super_admin_user(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    skill = session.get(Skill, skill_id)
    if not skill or skill.visibility != "public":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market skill not found")
    return _delete_skill_and_storage(session, skill)


@router.post("/artifacts/audio", response_model=ArtifactUploadResponse)
async def upload_audio_artifact(user: CurrentUserDep, file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio filename is required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".wav", ".mp3", ".flac", ".m4a", ".ogg"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported audio file type")
    staging_dir = ensure_storage_root() / "tmp" / "artifact-upload-staging" / uuid4().hex
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / Path(file.filename).name
    try:
        await save_upload_to_disk(file, staged_path)
        stored = store_existing_file_as_artifact(
            staged_path,
            kind="audio",
            metadata={"uploaded_by_user_id": user.id, "uploaded_by_account": user.account},
        )
        return ArtifactUploadResponse(**stored)
    finally:
        remove_tree(staging_dir)


@router.get("/artifacts/{artifact_id}")
async def get_artifact_manifest(artifact_id: str, user: CurrentUserDep):
    manifest = read_artifact_manifest(artifact_id)
    if not manifest or manifest.get("deleted"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    return manifest


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str, user: CurrentUserDep):
    manifest = read_artifact_manifest(artifact_id)
    if not manifest or manifest.get("deleted"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    content_path = manifest.get("content_path")
    if not isinstance(content_path, str) or not Path(content_path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact content not found")
    return FileResponse(Path(content_path), filename=str(manifest.get("filename") or Path(content_path).name))


@router.delete("/artifacts/{artifact_id}")
async def delete_uploaded_artifact(artifact_id: str, user: CurrentUserDep, mode: str = "soft"):
    if mode not in {"soft", "hard"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode must be soft or hard")
    result = delete_artifact(artifact_id, mode=mode)
    if artifact_id in result.get("missing_artifact_ids", []):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    if artifact_id in result.get("failed_artifact_ids", []):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Artifact deletion failed")
    return result


@router.get("/workspaces/{workspace_id}/agent-prompt", response_model=WorkspacePromptResponse)
async def build_workspace_agent_prompt(workspace_id: int, session: SessionDep, user: CurrentUserDep, authorization: str | None = Header(default=None)):
    if not await can_access_workspace(user, workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    membership = _workspace_membership(session, workspace, user.id)
    available_pairs = _available_skills_for_prompt(session, workspace, membership)
    skill_rows = []
    for skill, version in available_pairs:
        tools = _version_tools(session, version.id)
        skill_rows.append(
            {
                "skill_id": skill.id,
                "skill_name": skill.name,
                "description": skill.description or "",
                "version": version.version,
                "mcp_endpoint_url": version.published_mcp_endpoint_url,
                "tools": [tool.name for tool in tools],
            }
        )
    skill_lines = [
        f"- {item['skill_name']} (version {item['version']}): {item['mcp_endpoint_url']} | tools: {', '.join(item['tools'])}"
        for item in skill_rows
    ] or ["- No business skills are currently available."]
    global_tools = list_global_tool_definitions()
    global_tool_lines = [
        f"- {tool['name']}: {tool.get('description') or ''}"
        for tool in global_tools
    ]
    global_transfer_prompt_lines = [
        "Global transfer tools are available on every concrete skill MCP endpoint:",
        *global_tool_lines,
        _global_transfer_prompt_block(),
    ]
    access_token = _bearer_token_from_authorization(authorization)
    prompt_text = "\n".join(
        [
            f"Workspace: {workspace.name} (id={workspace.id})",
            "Use only the concrete skill MCP endpoints below.",
            f"Authentication: use this exact header: Authorization: Bearer {access_token}" if access_token else "Authentication: use Authorization: Bearer <workspace access token>.",
            "Do not omit the Authorization header. MCP endpoints return 401 Authentication required without it.",
            "MCP call sequence:",
            _mcp_call_prompt_block(),
            "Available skills:",
            *skill_lines,
            *global_transfer_prompt_lines,
            "Do not use the deprecated workspace MCP endpoint for tool calls.",
        ]
    )
    return WorkspacePromptResponse(
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        workspace_mcp_url="deprecated",
        prompt_text=prompt_text,
        available_skills=skill_rows,
        global_tools=global_tools,
    )


@router.get("/skills/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: int, request: Request, session: SessionDep, user: CurrentUserDep, authorization: str | None = Header(default=None)):
    skill, workspace = _require_skill_access(session, skill_id, user)
    return _build_skill_response(session, skill, workspace, _bearer_token_from_authorization(authorization), str(request.base_url).rstrip("/"))


@router.put("/skills/{skill_id}/prompt-config", response_model=MarketSkillResponse)
async def update_skill_prompt_config(
    skill_id: int,
    data: SkillPromptConfigUpdate,
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
):
    if not await can_review_skill(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    skill.handler_config = {
        **(skill.handler_config or {}),
        "prompt_content": data.prompt_content,
        "prompt_join_logic": data.prompt_join_logic,
    }
    skill.updated_at = datetime.utcnow()
    session.add(skill)
    session.commit()
    session.refresh(skill)
    return _build_market_skill_response(
        session,
        skill,
        _bearer_token_from_authorization(request.headers.get("authorization")),
        str(request.base_url).rstrip("/"),
    )


@router.get("/skills/{skill_id}/versions", response_model=list[SkillVersionResponse])
async def list_skill_versions(skill_id: int, session: SessionDep, user: CurrentUserDep):
    skill, _workspace = _require_skill_access(session, skill_id, user)
    versions = _skill_versions(session, skill.id)
    return [_build_version_response(session, version, skill) for version in versions]


@router.get("/skill-versions/{version_id}", response_model=SkillVersionResponse)
async def get_skill_version(version_id: int, session: SessionDep, user: CurrentUserDep):
    version = session.get(SkillVersion, version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill version not found")
    skill, _workspace = _require_skill_access(session, version.skill_id, user)
    return _build_version_response(session, version, skill)


@router.get("/skill-versions/{version_id}/download")
async def download_skill_version(version_id: int, session: SessionDep, user: CurrentUserDep):
    version = session.get(SkillVersion, version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill version not found")
    skill = session.get(Skill, version.skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.visibility != "public":
        _require_skill_access(session, version.skill_id, user)
    if not version.package_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")
    package_path = Path(version.package_path)
    if not package_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package not found")
    filename = version.upload_filename or f"skill-{version.skill_id}-{version.version}.zip"
    return FileResponse(package_path, media_type="application/zip", filename=filename)


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: int, session: SessionDep, user: CurrentUserDep):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not is_super_admin_user(user) and not await can_manage_workspace(user, skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return _delete_skill_and_storage(session, skill)


def _delete_skill_and_storage(session, skill: Skill) -> dict[str, bool]:
    versions = session.exec(select(SkillVersion).where(SkillVersion.skill_id == skill.id)).all()
    tools = session.exec(select(Tool).where(Tool.skill_id == skill.id)).all()
    exposures = session.exec(select(WorkspaceSkillExposure).where(WorkspaceSkillExposure.skill_id == skill.id)).all()
    memberships = session.exec(select(TeamMembership)).all()
    skill_id = skill.id
    if skill_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    skill.current_approved_version_id = None
    skill.deployed_version_id = None
    session.add(skill)
    session.flush()
    for membership in memberships:
        preferences = dict(membership.skill_preferences or {})
        if preferences.pop(str(skill_id), None) is not None:
            membership.skill_preferences = preferences
            session.add(membership)
    for tool in tools:
        session.delete(tool)
    for exposure in exposures:
        session.delete(exposure)
    for version in versions:
        session.delete(version)
    session.delete(skill)
    session.commit()
    for version in versions:
        cleanup_skill_version_storage(version.id)
        cleanup_review_workbench(version.id)
        cleanup_deployed_skill(skill_id, version.id)
    return {"ok": True}


@router.put("/skills/{skill_id}/visibility", response_model=SkillResponse)
async def update_skill_visibility(skill_id: int, data: SkillVisibilityUpdate, session: SessionDep, user: CurrentUserDep):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not await can_manage_workspace(user, skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    skill.visibility = _validate_visibility(data.visibility) or "private"
    skill.updated_at = datetime.utcnow()
    session.add(skill)
    session.commit()
    session.refresh(skill)
    workspace = session.get(Workspace, skill.workspace_id)
    return _build_skill_response(session, skill, workspace)


@router.post("/skill-versions/{version_id}/sync-to-workspace", response_model=SkillResponse)
async def sync_skill_version_to_workspace(version_id: int, data: SkillSyncRequest, session: SessionDep, user: CurrentUserDep):
    version = session.get(SkillVersion, version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill version not found")
    source_skill = session.get(Skill, version.skill_id)
    if not source_skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    _require_skill_access(session, source_skill.id, user)
    if not await can_install_to_workspace(user, data.target_workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace access required")

    target_workspace = session.get(Workspace, data.target_workspace_id)
    if not target_workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    target_skill = session.exec(
        select(Skill).where(Skill.workspace_id == target_workspace.id, Skill.name == source_skill.name)
    ).first()
    if target_skill is None:
        target_skill = Skill(
            workspace_id=target_workspace.id,
            name=source_skill.name,
            description=source_skill.description,
            visibility=_validate_visibility(data.visibility) or source_skill.visibility,
            handler_config={},
        )
        session.add(target_skill)
        session.commit()
        session.refresh(target_skill)
    else:
        if data.visibility is not None:
            target_skill.visibility = _validate_visibility(data.visibility) or target_skill.visibility
            target_skill.updated_at = datetime.utcnow()
            session.add(target_skill)

    duplicate = session.exec(
        select(SkillVersion).where(SkillVersion.skill_id == target_skill.id, SkillVersion.version == version.version)
    ).first()
    if duplicate:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Version already exists: {version.version}")

    if not version.package_path or not Path(version.package_path).exists():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill version package is not available for sync")
    if not version.extracted_path or not Path(version.extracted_path).exists():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill version extracted package is not available for sync")

    target_version = SkillVersion(
        skill_id=target_skill.id,
        version=version.version,
        status="uploaded",
        upload_filename=version.upload_filename,
        package_path=None,
        extracted_path=None,
        manifest_data=version.manifest_data or {},
        handler_config={},
        uploaded_by_user_id=user.id,
    )
    session.add(target_version)
    session.commit()
    session.refresh(target_version)

    target_version_dir = skill_version_storage_dir(target_version.id)
    remove_tree(target_version_dir)
    archive_path = target_version_dir / "package.zip"
    extracted_dir = target_version_dir / "package"
    target_version_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(version.package_path), archive_path)
    shutil.copytree(Path(version.extracted_path), extracted_dir)

    manifest_root = Path(version.extracted_path).resolve()
    package_root = extracted_dir
    source_handler = version.handler_config or {}
    rewritten_handler = _rewrite_path_value(source_handler, manifest_root, package_root)
    if isinstance(rewritten_handler, dict):
        target_version.handler_config = rewritten_handler
    else:
        target_version.handler_config = dict(source_handler)
    target_version.package_path = str(archive_path)
    target_version.extracted_path = str(package_root.resolve())
    target_version.updated_at = datetime.utcnow()
    target_skill.updated_at = datetime.utcnow()
    session.add(target_skill)
    session.add(target_version)
    _create_version_tools(session, target_skill, target_version, (version.manifest_data or {}).get("tools") or [])
    _ensure_team_exposure_row(session, target_workspace, target_skill)
    session.commit()
    session.refresh(target_skill)
    return _build_skill_response(session, target_skill, target_workspace)


@router.get("/review/skill-versions", response_model=list[ReviewSkillVersionResponse])
async def list_review_versions(session: SessionDep, user: CurrentUserDep, status_filter: str | None = None):
    if not await can_review_skill(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    statement = select(SkillVersion)
    if status_filter:
        statement = statement.where(SkillVersion.status == status_filter)
    versions = session.exec(statement).all()
    results: list[ReviewSkillVersionResponse] = []
    for version in versions:
        skill = session.get(Skill, version.skill_id)
        workspace = session.get(Workspace, skill.workspace_id) if skill else None
        if not skill or not workspace:
            continue
        results.append(_build_review_version_response(session, version, skill, workspace))
    return results


@router.post("/skill-versions/{version_id}/start-review", response_model=ReviewWorkbenchResponse)
async def start_review_workbench(version_id: int, session: SessionDep, user: CurrentUserDep):
    if not await can_review_skill(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    version = session.get(SkillVersion, version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill version not found")
    skill = session.get(Skill, version.skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    workspace = session.get(Workspace, skill.workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    package_path = Path(version.package_path).resolve() if version.package_path else None
    extracted_path = Path(version.extracted_path).resolve() if version.extracted_path else None
    if package_path is None and extracted_path is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill version has no deployable package snapshot")

    review_attempt_id = uuid4().hex
    review_root = ensure_storage_root() / "review-workbenches" / f"version-{version.id}" / f"attempt-{review_attempt_id}"
    package_copy_path = None
    extracted_copy_path = None
    review_root.mkdir(parents=True, exist_ok=True)
    if package_path is not None and package_path.exists():
        package_copy_path = review_root / "package.zip"
        shutil.copy2(package_path, package_copy_path)
    if extracted_path is not None and extracted_path.exists():
        extracted_copy_path = review_root / "package"
        shutil.copytree(extracted_path, extracted_copy_path)
    handler = version.handler_config or {}
    deployment_kind = str(handler.get("type") or "unknown")
    deployment_steps = [
        "Download the ZIP snapshot if you need to inspect the exact uploaded artifact.",
        "Review the extracted package and manifest in the prepared workbench directory.",
        "Validate handler configuration, tools, and runtime entrypoint before approval.",
        "Approve to make this version eligible for MCP exposure, or reject to keep it out of MCP.",
    ]
    if deployment_kind == "python_package":
        deployment_steps.insert(2, "Confirm the python_package entrypoint and package directory are deployable on the server.")

    return ReviewWorkbenchResponse(
        review_attempt_id=review_attempt_id,
        version=_build_review_version_response(session, version, skill, workspace),
        workbench_path=str(review_root),
        workbench_package_path=str(package_copy_path) if package_copy_path else None,
        workbench_extracted_path=str(extracted_copy_path) if extracted_copy_path else None,
        deployment_kind=deployment_kind,
        deployment_entrypoint=handler.get("entrypoint") if isinstance(handler.get("entrypoint"), str) else None,
        deployment_ready=bool(package_copy_path or extracted_copy_path),
        tool_count=len(_version_tools(session, version.id)),
        manifest_data=version.manifest_data or {},
        handler_config=handler,
        deployment_steps=deployment_steps,
    )


@router.post("/skill-versions/{version_id}/deploy", response_model=SkillVersionResponse)
async def deploy_skill_version(
    version_id: int,
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
    data: SkillDeployRequest | None = Body(default=None),
):
    if not await can_review_skill(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    version = session.get(SkillVersion, version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill version not found")
    skill = session.get(Skill, version.skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")

    payload = data or SkillDeployRequest()
    if payload.review_attempt_id:
        candidate = ensure_storage_root() / "review-workbenches" / f"version-{version.id}" / f"attempt-{payload.review_attempt_id}" / "package"
        if not candidate.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review attempt not found")
        source_path = candidate.resolve()
    else:
        source_path = _resolve_deploy_source(version)

    admin_workspace = ensure_admin_workspace(session, user)
    version.deploy_status = "deploying"
    version.deploy_error = None
    version.updated_at = datetime.utcnow()
    session.add(version)
    session.commit()

    try:
        deployment = deploy_skill_runtime(
            admin_workspace_id=admin_workspace.id,
            skill_id=skill.id,
            version_id=version.id,
            source_extracted_path=source_path,
            handler_config=version.handler_config or {},
            original_extracted_path=Path(version.extracted_path).resolve() if version.extracted_path else None,
        )
    except (SkillDeploymentError, OSError, subprocess.SubprocessError) as exc:
        version.deploy_status = "failed"
        version.deploy_error = str(exc)
        version.updated_at = datetime.utcnow()
        session.add(version)
        session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Deployment failed: {exc}") from exc

    version.deployment_path = deployment["deployment_path"]
    version.runtime_path = deployment["runtime_path"]
    version.venv_path = deployment["venv_path"]
    version.dependency_manifest = deployment["dependency_manifest"]
    version.deployed_handler_config = deployment["deployed_handler_config"]
    endpoint_url = payload.mcp_endpoint_url or f"{str(request.base_url).rstrip('/')}/mcp/{skill.workspace_id}/{skill.id}"
    version.published_mcp_endpoint_url = _validate_mcp_endpoint_url(endpoint_url)
    version.deploy_status = "deployed"
    version.deploy_error = None
    version.deployed_by_user_id = user.id
    version.deployed_at = datetime.utcnow()
    version.updated_at = datetime.utcnow()
    skill.deployed_version_id = version.id
    skill.updated_at = datetime.utcnow()
    session.add(version)
    session.add(skill)
    session.commit()
    session.refresh(version)
    return _build_version_response(session, version, skill)


@router.post("/skill-versions/{version_id}/approve", response_model=SkillVersionResponse)
async def approve_skill_version(version_id: int, session: SessionDep, user: CurrentUserDep):
    if not await can_review_skill(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    version = session.get(SkillVersion, version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill version not found")
    skill = session.get(Skill, version.skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    versions = _skill_versions(session, skill.id)
    for item in versions:
        if item.id == version.id:
            item.status = "approved"
        elif item.status == "approved":
            item.status = "archived"
        item.updated_at = datetime.utcnow()
        session.add(item)
    skill.current_approved_version_id = version.id
    skill.updated_at = datetime.utcnow()
    session.add(skill)
    session.commit()
    session.refresh(version)
    return _build_version_response(session, version, skill)


@router.post("/skill-versions/{version_id}/reject", response_model=SkillVersionResponse)
async def reject_skill_version(version_id: int, session: SessionDep, user: CurrentUserDep):
    if not await can_review_skill(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    version = session.get(SkillVersion, version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill version not found")
    skill = session.get(Skill, version.skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    version.status = "rejected"
    version.updated_at = datetime.utcnow()
    if skill.current_approved_version_id == version.id:
        skill.current_approved_version_id = None
        skill.updated_at = datetime.utcnow()
        session.add(skill)
    session.add(version)
    session.commit()
    session.refresh(version)
    return _build_version_response(session, version, skill)


@router.post("/skills/{skill_id}/clear-approved-version", response_model=SkillResponse)
async def clear_approved_skill_version(skill_id: int, session: SessionDep, user: CurrentUserDep):
    if not await can_review_skill(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.current_approved_version_id:
        version = session.get(SkillVersion, skill.current_approved_version_id)
        if version:
            version.status = "archived"
            version.updated_at = datetime.utcnow()
            session.add(version)
    skill.current_approved_version_id = None
    skill.updated_at = datetime.utcnow()
    session.add(skill)
    session.commit()
    session.refresh(skill)
    workspace = session.get(Workspace, skill.workspace_id)
    return _build_skill_response(session, skill, workspace)


@router.get("/workspaces/{workspace_id}/approved-skills", response_model=list[SkillExposureItem])
async def list_approved_skills(workspace_id: int, session: SessionDep, user: CurrentUserDep):
    if not await can_access_workspace(user, workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    skills = session.exec(select(Skill).where(Skill.workspace_id == workspace_id)).all()
    items: list[SkillExposureItem] = []
    for skill in skills:
        if skill.visibility == "public":
            continue
        version = current_runtime_version(session, skill)
        if version is None:
            continue
        items.append(
            SkillExposureItem(
                skill_id=skill.id,
                name=skill.name,
                description=skill.description,
                enabled=workspace_skill_exposure_enabled(session, workspace, skill.id),
                current_approved_version_id=version.id,
                current_approved_version=version.version,
            )
        )
    return items


@router.get("/workspaces/{workspace_id}/skill-exposure", response_model=list[SkillExposureItem])
async def list_skill_exposure(workspace_id: int, session: SessionDep, user: CurrentUserDep):
    workspace = _require_team_admin(session, workspace_id, user.id)
    skills = session.exec(select(Skill).where(Skill.workspace_id == workspace.id)).all()
    return [
        SkillExposureItem(
            skill_id=skill.id,
            name=skill.name,
            description=skill.description,
            enabled=workspace_skill_exposure_enabled(session, workspace, skill.id),
            current_approved_version_id=current_runtime_version(session, skill).id,
            current_approved_version=current_runtime_version(session, skill).version,
        )
        for skill in skills
        if skill.visibility != "public" and current_runtime_version(session, skill) is not None
    ]


@router.put("/workspaces/{workspace_id}/skill-exposure", response_model=list[SkillExposureItem])
async def update_skill_exposure(
    workspace_id: int,
    data: SkillExposureUpdate,
    session: SessionDep,
    user: CurrentUserDep,
):
    workspace = _require_team_admin(session, workspace_id, user.id)
    skills = session.exec(select(Skill).where(Skill.workspace_id == workspace.id)).all()
    skills = [skill for skill in skills if skill.visibility != "public" and current_runtime_version(session, skill) is not None]
    allowed_ids = {skill.id for skill in skills}
    invalid = sorted(set(data.enabled_skill_ids) - allowed_ids)
    if invalid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown approved skills: {invalid}")

    enabled_ids = set(data.enabled_skill_ids)
    for skill in skills:
        exposure = session.get(WorkspaceSkillExposure, (workspace.id, skill.id))
        if exposure is None:
            exposure = WorkspaceSkillExposure(workspace_id=workspace.id, skill_id=skill.id, enabled=False)
        exposure.enabled = skill.id in enabled_ids
        exposure.updated_at = datetime.utcnow()
        session.add(exposure)
    session.commit()
    return await list_skill_exposure(workspace_id, session, user)


@router.get("/workspaces/{workspace_id}/skill-availability", response_model=list[SkillExposureItem])
async def list_skill_availability_alias(workspace_id: int, session: SessionDep, user: CurrentUserDep):
    return await list_skill_exposure(workspace_id, session, user)


@router.put("/workspaces/{workspace_id}/skill-availability", response_model=list[SkillExposureItem])
async def update_skill_availability_alias(
    workspace_id: int,
    data: SkillExposureUpdate,
    session: SessionDep,
    user: CurrentUserDep,
):
    return await update_skill_exposure(workspace_id, data, session, user)
