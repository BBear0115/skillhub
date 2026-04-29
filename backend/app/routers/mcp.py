import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlmodel import select

from app.core.permissions import current_runtime_version, is_public_runtime_skill, team_member_skill_enabled, workspace_skill_exposure_enabled
from app.database import get_session
from app.models import Skill, SkillVersion, TeamMembership, Tool, Workspace
from app.services import mcp_protocol
from app.services.global_transfer_tools import execute_global_tool, list_global_tool_definitions
from app.services.skill_runner import execute_tool

router = APIRouter()


def _global_tool_names() -> set[str]:
    return {tool["name"] for tool in list_global_tool_definitions()}


def _is_repo_handler(handler_type: str | None) -> bool:
    return handler_type in {"skill_repo_exec", "marketplace_repo"}


def _runtime_handler(version: SkillVersion) -> dict[str, Any]:
    return version.deployed_handler_config or version.handler_config or {}


def _jsonrpc_result(req_id, result: dict, headers: dict[str, str] | None = None, status_code: int = 200) -> Response:
    return Response(
        content=json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}),
        media_type="application/json",
        headers=headers,
        status_code=status_code,
    )


def _jsonrpc_error(req_id, code: int, message: str, status_code: int = 400) -> Response:
    return Response(
        content=json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}),
        media_type="application/json",
        status_code=status_code,
    )


def _workspace_membership(session, workspace: Workspace, user_id: int) -> TeamMembership | None:
    if workspace.type != "team" or workspace.team_id is None:
        return None
    return session.exec(
        select(TeamMembership).where(
            TeamMembership.team_id == workspace.team_id,
            TeamMembership.user_id == user_id,
        )
    ).first()


async def _resolve_workspace_access(workspace_id: int, authorization: str | None) -> tuple[Workspace, int, TeamMembership | None]:
    auth_context = await mcp_protocol.get_auth_context(authorization)
    if auth_context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    user_id = auth_context["user_id"]
    key_workspace_id = auth_context.get("workspace_id")
    if key_workspace_id is not None and key_workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    for session in get_session():
        workspace = session.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        if workspace.type == "personal":
            if workspace.owner_id != user_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
            return workspace, user_id, None
        if workspace.type == "admin":
            if workspace.owner_id != user_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
            return workspace, user_id, None
        membership = _workspace_membership(session, workspace, user_id)
        if not membership:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return workspace, user_id, membership

    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Workspace lookup failed")


async def _resolve_skill_access(workspace_id: int, skill_id: int, authorization: str | None) -> tuple[Skill, SkillVersion, int]:
    auth_context = await mcp_protocol.get_auth_context(authorization)
    if auth_context is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    user_id = auth_context["user_id"]
    for session in get_session():
        workspace = session.get(Workspace, workspace_id)
        if not workspace:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
        skill = session.get(Skill, skill_id)
        if not skill:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        version = current_runtime_version(session, skill)
        if version is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        membership = _workspace_membership(session, workspace, user_id)
        if skill.workspace_id == workspace_id:
            if is_public_runtime_skill(session, skill):
                return skill, version, user_id
            if workspace.type == "personal" and workspace.owner_id != user_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
            if workspace.type == "team" and not membership:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
            if workspace.type == "team" and skill.visibility != "public":
                if not workspace_skill_exposure_enabled(session, workspace, skill.id):
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
                if not team_member_skill_enabled(membership, skill.id):
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
            return skill, version, user_id
        if workspace.type == "personal" and workspace.owner_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        if workspace.type == "team" and not membership:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        if not is_public_runtime_skill(session, skill):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        if workspace.type == "team" and not team_member_skill_enabled(membership, skill.id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        return skill, version, user_id
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Skill lookup failed")


def _validate_session(mcp_session_id: str | None, workspace_id: int, mode: str) -> dict:
    sess = mcp_protocol.get_session_data(mcp_session_id) if mcp_session_id else None
    if not sess:
        raise ValueError("Invalid session")
    if sess.get("workspace_id") != workspace_id or sess.get("mode") != mode:
        raise ValueError("Session does not match this endpoint")
    return sess


def _tool_payload(tool: Tool) -> dict:
    return {
        "name": tool.name,
        "description": tool.description or "",
        "inputSchema": tool.input_schema or {},
    }


def _workspace_skill_payload(skill: Skill, tools: list[Tool]) -> dict:
    return {
        "skill_id": skill.id,
        "skill_name": skill.name,
        "description": skill.description or "",
        "tools": [_tool_payload(tool) for tool in tools],
    }


def _workspace_visible_skills(session, workspace: Workspace, membership: TeamMembership | None) -> list[tuple[Skill, SkillVersion]]:
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


def _resolve_workspace_skill(session, workspace: Workspace, membership: TeamMembership | None, args: dict) -> Skill | None:
    visible_skills = _workspace_visible_skills(session, workspace, membership)
    skill_id = args.get("skill_id")
    if isinstance(skill_id, int):
        for skill, _version in visible_skills:
            if skill.id == skill_id:
                return skill

    skill_name = args.get("skill_name")
    if isinstance(skill_name, str) and skill_name.strip():
        for skill, _version in visible_skills:
            if skill.name == skill_name.strip():
                return skill
    return None


def _normalize_tool_token(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized or "tool"


def _workspace_alias(skill: Skill, tool: Tool) -> str:
    return f"skill_{skill.id}_{_normalize_tool_token(tool.name)}"


def _skill_resource_uri(skill: Skill, version: SkillVersion, tool_name: str | None = None) -> str | None:
    handler = _runtime_handler(version)
    if _is_repo_handler(handler.get("type")):
        if tool_name:
            return f"skillhub://workspaces/{skill.workspace_id}/skills/{skill.id}/docs/{_normalize_tool_token(tool_name)}"
        return None
    doc_path = handler.get("doc_path")
    if isinstance(doc_path, str) and doc_path:
        return f"skillhub://workspaces/{skill.workspace_id}/skills/{skill.id}/skill-doc"
    return None


def _read_skill_doc(version: SkillVersion, tool_name: str | None = None) -> str | None:
    handler = _runtime_handler(version)
    handler_type = handler.get("type")

    if _is_repo_handler(handler_type):
        plugins = handler.get("plugins", {})
        plugin = plugins.get(tool_name) if tool_name else None
        if plugin is None and len(plugins) == 1:
            plugin = next(iter(plugins.values()))
        if isinstance(plugin, dict):
            skill_doc = plugin.get("skill_doc")
            if isinstance(skill_doc, str) and Path(skill_doc).exists():
                return Path(skill_doc).read_text(encoding="utf-8")

    doc_path = handler.get("doc_path")
    if isinstance(doc_path, str) and Path(doc_path).exists():
        return Path(doc_path).read_text(encoding="utf-8")
    return None


def _resource_payload(skill: Skill, version: SkillVersion, tool_name: str | None = None) -> dict | None:
    uri = _skill_resource_uri(skill, version, tool_name)
    if uri is None:
        return None
    description = skill.description or f"Instructions for {skill.name}"
    if tool_name:
        description = f"Instructions for calling {tool_name} in {skill.name}"
    return {
        "uri": uri,
        "name": f"{skill.name} instructions",
        "description": description,
        "mimeType": "text/markdown",
    }


def _collect_visible_workspace_tools(session, workspace: Workspace, membership: TeamMembership | None) -> list[tuple[Skill, SkillVersion, Tool]]:
    skill_versions = _workspace_visible_skills(session, workspace, membership)
    if not skill_versions:
        return []

    tool_rows = session.exec(
        select(Tool).where(Tool.skill_version_id.in_([version.id for _, version in skill_versions]))
    ).all()
    version_map = {version.id: (skill, version) for skill, version in skill_versions}
    ordered_pairs: list[tuple[Skill, SkillVersion, Tool]] = []
    for tool in sorted(tool_rows, key=lambda item: (item.skill_id, item.id or 0)):
        if tool.skill_version_id is None:
            continue
        resolved = version_map.get(tool.skill_version_id)
        if resolved is None:
            continue
        skill, version = resolved
        ordered_pairs.append((skill, version, tool))
    return ordered_pairs


def _workspace_tool_definitions(pairs: list[tuple[Skill, SkillVersion, Tool]]) -> list[dict]:
    definitions: list[dict] = [
        {
            "name": "skills_list",
            "description": "List business skills currently visible in this workspace.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "skill_call",
            "description": "Call a business skill by skill_id or skill_name and a nested tool_name.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "integer"},
                    "skill_name": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
                "required": ["tool_name"],
                "additionalProperties": False,
            },
        },
    ]
    for skill, _version, tool in pairs:
        descriptions = [part for part in [skill.name, tool.description or skill.description or ""] if part]
        definitions.append(
            {
                "name": _workspace_alias(skill, tool),
                "description": " | ".join(descriptions),
                "inputSchema": tool.input_schema or {},
            }
        )
    return definitions


def _workspace_tool_lookup(pairs: list[tuple[Skill, SkillVersion, Tool]]) -> dict[str, tuple[Skill, SkillVersion, Tool]]:
    return {_workspace_alias(skill, tool): (skill, version, tool) for skill, version, tool in pairs}


@router.post("/workspaces/{workspace_id}")
async def workspace_mcp_post(
    workspace_id: int,
    request: Request,
    authorization: str | None = Header(default=None),
    mcp_session_id: str | None = Header(default=None, alias="Mcp-Session-Id"),
):
    body = await request.json()
    req_id = body.get("id")
    await _resolve_workspace_access(workspace_id, authorization)
    return _jsonrpc_error(
        req_id,
        -32004,
        "Workspace MCP is deprecated. Use the concrete skill MCP endpoint /mcp/{workspace_id}/{skill_id}.",
        status_code=status.HTTP_410_GONE,
    )


@router.get("/workspaces/{workspace_id}")
async def workspace_mcp_get(
    workspace_id: int,
    authorization: str | None = Header(default=None),
    mcp_session_id: str | None = Header(default=None, alias="Mcp-Session-Id"),
):
    await _resolve_workspace_access(workspace_id, authorization)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Workspace MCP is deprecated. Use /mcp/{workspace_id}/{skill_id}.",
    )


@router.delete("/workspaces/{workspace_id}")
async def workspace_mcp_delete(
    workspace_id: int,
    mcp_session_id: str | None = Header(default=None, alias="Mcp-Session-Id"),
):
    if mcp_session_id:
        session_data = mcp_protocol.get_session_data(mcp_session_id)
        if session_data and session_data.get("workspace_id") == workspace_id and session_data.get("mode") == "workspace":
            mcp_protocol.delete_session(mcp_session_id)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/{workspace_id}/{skill_id}")
async def mcp_post(
    workspace_id: int,
    skill_id: int,
    request: Request,
    authorization: str | None = Header(default=None),
    mcp_session_id: str | None = Header(default=None, alias="Mcp-Session-Id"),
):
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")
    skill, version, user_id = await _resolve_skill_access(workspace_id, skill_id, authorization)

    if method == "initialize":
        new_session_id = mcp_protocol.create_session(skill.id, workspace_id, user_id, mode="skill")
        return _jsonrpc_result(req_id, mcp_protocol.build_initialize_result(skill), headers={"Mcp-Session-Id": new_session_id})

    if method == "notifications/initialized":
        return _jsonrpc_result(req_id, {})

    try:
        _validate_session(mcp_session_id, workspace_id, "skill")
    except ValueError as exc:
        return _jsonrpc_error(req_id, -32001, str(exc))

    if method == "ping":
        return _jsonrpc_result(req_id, {"ok": True})

    for session in get_session():
        skill_tools = session.exec(select(Tool).where(Tool.skill_version_id == version.id)).all()

        if method == "tools/list":
            payload = mcp_protocol.build_tools_list(skill_tools)
            payload["tools"].extend(list_global_tool_definitions())
            return _jsonrpc_result(req_id, payload)

        if method == "resources/list":
            resources = []
            if _is_repo_handler((_runtime_handler(version)).get("type")):
                for tool in skill_tools:
                    resource = _resource_payload(skill, version, tool.name)
                    if resource:
                        resources.append(resource)
            else:
                resource = _resource_payload(skill, version)
                if resource:
                    resources.append(resource)
            return _jsonrpc_result(req_id, mcp_protocol.build_resources_list(resources))

        if method == "resources/read":
            params = body.get("params", {})
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                return _jsonrpc_error(req_id, -32602, "Resource not found")
            matched_tool_name: str | None = None
            if _is_repo_handler((_runtime_handler(version)).get("type")):
                for tool in skill_tools:
                    if _skill_resource_uri(skill, version, tool.name) == uri:
                        matched_tool_name = tool.name
                        break
                if matched_tool_name is None:
                    return _jsonrpc_error(req_id, -32602, "Resource not found")
            else:
                expected = _skill_resource_uri(skill, version)
                if expected != uri:
                    return _jsonrpc_error(req_id, -32602, "Resource not found")
            text = _read_skill_doc(version, matched_tool_name)
            if text is None:
                return _jsonrpc_error(req_id, -32602, "Resource not found")
            return _jsonrpc_result(req_id, mcp_protocol.build_resource_read_result(uri, text))

        if method == "tools/call":
            params = body.get("params", {})
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                return _jsonrpc_error(req_id, -32602, "Tool arguments must be an object")
            if isinstance(tool_name, str) and tool_name in _global_tool_names():
                return _jsonrpc_result(req_id, mcp_protocol.build_tool_result(execute_global_tool(tool_name, arguments)))
            tool = session.exec(select(Tool).where(Tool.skill_version_id == version.id, Tool.name == tool_name)).first()
            if not tool:
                return _jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}")
            exec_result = await execute_tool(_runtime_handler(version), tool, arguments)
            return _jsonrpc_result(req_id, mcp_protocol.build_tool_result(exec_result))

        return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")

    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/{workspace_id}/{skill_id}")
async def mcp_get(
    workspace_id: int,
    skill_id: int,
    authorization: str | None = Header(default=None),
    mcp_session_id: str | None = Header(default=None, alias="Mcp-Session-Id"),
):
    await _resolve_skill_access(workspace_id, skill_id, authorization)
    try:
        _validate_session(mcp_session_id, workspace_id, "skill")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    async def event_stream():
        yield "data: {}\n\n".format(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.delete("/{workspace_id}/{skill_id}")
async def mcp_delete(
    workspace_id: int,
    skill_id: int,
    mcp_session_id: str | None = Header(default=None, alias="Mcp-Session-Id"),
):
    if mcp_session_id:
        session_data = mcp_protocol.get_session_data(mcp_session_id)
        if (
            session_data
            and session_data.get("workspace_id") == workspace_id
            and session_data.get("skill_id") == skill_id
            and session_data.get("mode") == "skill"
        ):
            mcp_protocol.delete_session(mcp_session_id)
    return Response(status_code=status.HTTP_202_ACCEPTED)
