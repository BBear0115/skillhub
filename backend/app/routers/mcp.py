import json
import re
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlmodel import select

from app.core.permissions import is_skill_visible_in_workspace
from app.database import get_session
from app.models import Skill, TeamMembership, Tool, Workspace
from app.services import mcp_protocol
from app.services.skill_runner import execute_tool

router = APIRouter()


def _is_repo_handler(handler_type: str | None) -> bool:
    return handler_type in {"skill_repo_exec", "marketplace_repo"}


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
        membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == workspace.team_id,
                TeamMembership.user_id == user_id,
            )
        ).first()
        if not membership:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return workspace, user_id, membership

    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Workspace lookup failed")


async def _resolve_skill_access(workspace_id: int, skill_id: int, authorization: str | None) -> tuple[Skill, int]:
    workspace, user_id, membership = await _resolve_workspace_access(workspace_id, authorization)
    for session in get_session():
        skill = session.get(Skill, skill_id)
        if not skill or skill.workspace_id != workspace_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        if not is_skill_visible_in_workspace(workspace, skill, membership):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        return skill, user_id
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


def _resolve_workspace_skill(session, workspace_id: int, args: dict) -> Skill | None:
    skill_id = args.get("skill_id")
    if isinstance(skill_id, int):
        return session.exec(select(Skill).where(Skill.workspace_id == workspace_id, Skill.id == skill_id)).first()

    skill_name = args.get("skill_name")
    if isinstance(skill_name, str) and skill_name.strip():
        return session.exec(select(Skill).where(Skill.workspace_id == workspace_id, Skill.name == skill_name.strip())).first()
    return None


def _normalize_tool_token(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized or "tool"


def _workspace_alias(skill: Skill, tool: Tool) -> str:
    return f"skill_{skill.id}_{_normalize_tool_token(tool.name)}"


def _skill_resource_uri(skill: Skill, tool_name: str | None = None) -> str | None:
    handler = skill.handler_config or {}
    if _is_repo_handler(handler.get("type")):
        if tool_name:
            return f"skillhub://workspaces/{skill.workspace_id}/skills/{skill.id}/docs/{_normalize_tool_token(tool_name)}"
        return None
    doc_path = handler.get("doc_path")
    if isinstance(doc_path, str) and doc_path:
        return f"skillhub://workspaces/{skill.workspace_id}/skills/{skill.id}/skill-doc"
    return None


def _read_skill_doc(skill: Skill, tool_name: str | None = None) -> str | None:
    handler = skill.handler_config or {}
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


def _resource_payload(skill: Skill, tool_name: str | None = None) -> dict | None:
    uri = _skill_resource_uri(skill, tool_name)
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


def _collect_visible_workspace_tools(session, workspace: Workspace, membership: TeamMembership | None) -> list[tuple[Skill, Tool]]:
    skills = session.exec(select(Skill).where(Skill.workspace_id == workspace.id)).all()
    visible_skills = [skill for skill in skills if is_skill_visible_in_workspace(workspace, skill, membership)]
    tool_rows = (
        session.exec(select(Tool).where(Tool.skill_id.in_([skill.id for skill in visible_skills if skill.id is not None]))).all()
        if visible_skills
        else []
    )
    skill_map = {skill.id: skill for skill in visible_skills if skill.id is not None}
    ordered_pairs: list[tuple[Skill, Tool]] = []
    for tool in sorted(tool_rows, key=lambda item: (item.skill_id, item.id or 0)):
        skill = skill_map.get(tool.skill_id)
        if skill is not None:
            ordered_pairs.append((skill, tool))
    return ordered_pairs


def _workspace_tool_definitions(pairs: list[tuple[Skill, Tool]]) -> list[dict]:
    definitions: list[dict] = []
    for skill, tool in pairs:
        descriptions = [part for part in [skill.name, tool.description or skill.description or ""] if part]
        definitions.append(
            {
                "name": _workspace_alias(skill, tool),
                "description": " | ".join(descriptions),
                "inputSchema": tool.input_schema or {},
            }
        )
    return definitions


def _workspace_tool_lookup(pairs: list[tuple[Skill, Tool]]) -> dict[str, tuple[Skill, Tool]]:
    return {_workspace_alias(skill, tool): (skill, tool) for skill, tool in pairs}


@router.post("/workspaces/{workspace_id}")
async def workspace_mcp_post(
    workspace_id: int,
    request: Request,
    authorization: str | None = Header(default=None),
    mcp_session_id: str | None = Header(default=None, alias="Mcp-Session-Id"),
):
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")
    workspace, user_id, membership = await _resolve_workspace_access(workspace_id, authorization)

    if method == "initialize":
        new_session_id = mcp_protocol.create_session(None, workspace_id, user_id, mode="workspace")
        result = mcp_protocol.build_workspace_initialize_result(workspace)
        return _jsonrpc_result(req_id, result, headers={"Mcp-Session-Id": new_session_id})

    if method == "notifications/initialized":
        return _jsonrpc_result(req_id, {})

    try:
        _validate_session(mcp_session_id, workspace_id, "workspace")
    except ValueError as exc:
        return _jsonrpc_error(req_id, -32001, str(exc))

    if method == "ping":
        return _jsonrpc_result(req_id, {"ok": True})

    for session in get_session():
        visible_pairs = _collect_visible_workspace_tools(session, workspace, membership)
        tool_lookup = _workspace_tool_lookup(visible_pairs)

        if method == "tools/list":
            return _jsonrpc_result(req_id, mcp_protocol.build_workspace_tools_list(_workspace_tool_definitions(visible_pairs)))

        if method == "resources/list":
            resources = []
            seen_uris: set[str] = set()
            for skill, tool in visible_pairs:
                resource = _resource_payload(skill, tool.name)
                if resource and resource["uri"] not in seen_uris:
                    resources.append(resource)
                    seen_uris.add(resource["uri"])
            return _jsonrpc_result(req_id, mcp_protocol.build_resources_list(resources))

        if method == "resources/read":
            params = body.get("params", {})
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                return _jsonrpc_error(req_id, -32602, "uri is required")
            for skill, tool in visible_pairs:
                resource = _resource_payload(skill, tool.name)
                if resource and resource["uri"] == uri:
                    text = _read_skill_doc(skill, tool.name)
                    if text is None:
                        return _jsonrpc_error(req_id, -32602, "Resource not found")
                    return _jsonrpc_result(req_id, mcp_protocol.build_resource_read_result(uri, text))
            return _jsonrpc_error(req_id, -32602, "Resource not found")

        if method != "tools/call":
            return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")

        params = body.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return _jsonrpc_error(req_id, -32602, "Tool arguments must be an object")

        if tool_name == "skills_list":
            tools_by_skill_id: dict[int, list[Tool]] = {}
            for skill, tool in visible_pairs:
                tools_by_skill_id.setdefault(skill.id, []).append(tool)
            ordered_skills: list[Skill] = []
            seen_skill_ids: set[int] = set()
            for skill, _ in visible_pairs:
                if skill.id not in seen_skill_ids:
                    ordered_skills.append(skill)
                    seen_skill_ids.add(skill.id)
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"skills": [_workspace_skill_payload(skill, tools_by_skill_id.get(skill.id, [])) for skill in ordered_skills]},
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
                "isError": False,
            }
            return _jsonrpc_result(req_id, result)

        if tool_name == "skill_call":
            skill = _resolve_workspace_skill(session, workspace_id, arguments)
            if not skill or not is_skill_visible_in_workspace(workspace, skill, membership):
                return _jsonrpc_error(req_id, -32602, "Skill not found")
            nested_tool_name = arguments.get("tool_name")
            nested_arguments = arguments.get("arguments", {})
            if not isinstance(nested_tool_name, str) or not nested_tool_name:
                return _jsonrpc_error(req_id, -32602, "tool_name is required")
            if not isinstance(nested_arguments, dict):
                return _jsonrpc_error(req_id, -32602, "arguments must be an object")
            tool = session.exec(select(Tool).where(Tool.skill_id == skill.id, Tool.name == nested_tool_name)).first()
            if not tool:
                return _jsonrpc_error(req_id, -32602, f"Unknown tool: {nested_tool_name}")
            exec_result = await execute_tool(skill, tool, nested_arguments)
            return _jsonrpc_result(req_id, mcp_protocol.build_tool_result(exec_result))

        resolved = tool_lookup.get(tool_name) if isinstance(tool_name, str) else None
        if resolved is None:
            return _jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}")
        skill, tool = resolved
        exec_result = await execute_tool(skill, tool, arguments)
        return _jsonrpc_result(req_id, mcp_protocol.build_tool_result(exec_result))

    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/workspaces/{workspace_id}")
async def workspace_mcp_get(
    workspace_id: int,
    authorization: str | None = Header(default=None),
    mcp_session_id: str | None = Header(default=None, alias="Mcp-Session-Id"),
):
    await _resolve_workspace_access(workspace_id, authorization)
    try:
        _validate_session(mcp_session_id, workspace_id, "workspace")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    async def event_stream():
        yield "data: {}\n\n".format(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
    skill, user_id = await _resolve_skill_access(workspace_id, skill_id, authorization)

    if method == "initialize":
        new_session_id = mcp_protocol.create_session(skill_id, workspace_id, user_id, mode="skill")
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
        skill_tools = session.exec(select(Tool).where(Tool.skill_id == skill_id)).all()

        if method == "tools/list":
            return _jsonrpc_result(req_id, mcp_protocol.build_tools_list(skill_tools))

        if method == "resources/list":
            resources = []
            if _is_repo_handler((skill.handler_config or {}).get("type")):
                for tool in skill_tools:
                    resource = _resource_payload(skill, tool.name)
                    if resource:
                        resources.append(resource)
            else:
                resource = _resource_payload(skill)
                if resource:
                    resources.append(resource)
            return _jsonrpc_result(req_id, mcp_protocol.build_resources_list(resources))

        if method == "resources/read":
            params = body.get("params", {})
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                return _jsonrpc_error(req_id, -32602, "Resource not found")
            matched_tool_name: str | None = None
            if _is_repo_handler((skill.handler_config or {}).get("type")):
                for tool in skill_tools:
                    if _skill_resource_uri(skill, tool.name) == uri:
                        matched_tool_name = tool.name
                        break
                if matched_tool_name is None:
                    return _jsonrpc_error(req_id, -32602, "Resource not found")
            else:
                expected = _skill_resource_uri(skill)
                if expected != uri:
                    return _jsonrpc_error(req_id, -32602, "Resource not found")
            text = _read_skill_doc(skill, matched_tool_name)
            if text is None:
                return _jsonrpc_error(req_id, -32602, "Resource not found")
            return _jsonrpc_result(req_id, mcp_protocol.build_resource_read_result(uri, text))

        if method == "tools/call":
            params = body.get("params", {})
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                return _jsonrpc_error(req_id, -32602, "Tool arguments must be an object")
            tool = session.exec(select(Tool).where(Tool.skill_id == skill_id, Tool.name == tool_name)).first()
            if not tool:
                return _jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}")
            exec_result = await execute_tool(skill, tool, arguments)
            return _jsonrpc_result(req_id, mcp_protocol.build_tool_result(exec_result))

    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")


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
