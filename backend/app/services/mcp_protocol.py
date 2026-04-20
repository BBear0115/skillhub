from datetime import datetime, timezone
import hashlib
from typing import Any
from uuid import uuid4

from sqlmodel import select

from app.database import get_session
from app.models import ApiKey, Skill, Tool, Workspace
from app.core.security import decode_access_token

# In-memory session store (replace with Redis in production)
sessions: dict[str, dict[str, Any]] = {}


async def validate_api_key(key: str) -> dict[str, Any] | None:
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    for session in get_session():
        keys = session.exec(select(ApiKey)).all()
        for api_key in keys:
            if api_key.key_hash == key_hash:
                return {"user_id": api_key.user_id, "workspace_id": api_key.workspace_id, "auth_type": "api_key"}
    return None


async def get_auth_context(authorization: str | None) -> dict[str, Any] | None:
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        token = authorization[7:]
        payload = decode_access_token(token)
        if payload and payload.get("sub"):
            return {"user_id": int(payload["sub"]), "workspace_id": None, "auth_type": "access_token"}
        auth_context = await validate_api_key(token)
        return auth_context
    return None


def build_initialize_result(skill: Skill) -> dict[str, Any]:
    return {
        "protocolVersion": "2025-03-26",
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {},
            "prompts": {},
        },
        "serverInfo": {
            "name": skill.name,
            "version": "0.1.0",
        },
    }


def build_workspace_initialize_result(workspace: Workspace) -> dict[str, Any]:
    return {
        "protocolVersion": "2025-03-26",
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {},
            "prompts": {},
        },
        "serverInfo": {
            "name": f"{workspace.name} workspace",
            "version": "0.1.0",
        },
    }


def build_tools_list(tools: list[Tool]) -> dict[str, Any]:
    tools = [
        {
            "name": tool.name,
            "description": tool.description or "",
            "inputSchema": tool.input_schema or {},
        }
        for tool in tools
    ]
    return {"tools": tools}


def build_workspace_tools_list() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": "skills_list",
                "description": "List every skill currently available in this workspace, including each skill's tools and MCP endpoint.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "skill_tools",
                "description": "Show the tools exposed by one skill in this workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "skill_id": {"type": "integer"},
                        "skill_name": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "skill_call",
                "description": "Call a tool from one skill through the workspace aggregator.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "skill_id": {"type": "integer"},
                        "skill_name": {"type": "string"},
                        "tool_name": {"type": "string"},
                        "arguments": {"type": "object"},
                    },
                    "required": ["tool_name"],
                    "additionalProperties": False,
                },
            },
        ]
    }


def build_tool_result(result: dict) -> dict[str, Any]:
    return {
        "content": result.get("content", []),
        "isError": result.get("isError", False),
    }


def create_session(skill_id: int | None, workspace_id: int, user_id: int | None, mode: str = "skill") -> str:
    session_id = str(uuid4())
    sessions[session_id] = {
        "skill_id": skill_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return session_id


def get_session_data(session_id: str) -> dict[str, Any] | None:
    return sessions.get(session_id)


def delete_session(session_id: str) -> None:
    sessions.pop(session_id, None)
