import io
import json
import shutil
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from app.main import app
from app.services import mcp_protocol
import app.database as database_module
from app.config import settings


def _zip_single_skill_package(root: Path) -> io.BytesIO:
    package_root = root / "single-skill"
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "skill.json").write_text(
        json.dumps(
            {
                "name": "Echo Skill",
                "description": "Echo text from backend execution",
                "handler": {"type": "python_package", "entrypoint": "main.py:handle_tool"},
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (package_root / "main.py").write_text(
        "def handle_tool(context):\n"
        "    text = context['arguments'].get('text', '')\n"
        "    return {'content': [{'type': 'text', 'text': f'echo:{text}'}], 'isError': False}\n",
        encoding="utf-8",
    )
    (package_root / "SKILL.md").write_text(
        "# Echo Skill\n\nUse this skill when the caller needs a plain echo response.\n",
        encoding="utf-8",
    )
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path in package_root.rglob("*"):
            archive.writestr(str(path.relative_to(package_root)).replace("\\", "/"), path.read_bytes())
    buffer.seek(0)
    return buffer


def _zip_exec_repo(root: Path) -> io.BytesIO:
    repo_root = root / "exec-repo"
    plugin_root = repo_root / "skills" / "incident_reporter"
    (repo_root / ".codex").mkdir(parents=True, exist_ok=True)
    plugin_root.mkdir(parents=True, exist_ok=True)
    (repo_root / ".codex" / "skills-index.json").write_text(
        json.dumps(
            {
                "name": "Ops Skills",
                "description": "Operational backend-executed skills",
                "skills": [
                    {
                        "name": "incident_reporter",
                        "source": "../skills/incident_reporter",
                        "description": "Produce incident reports",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (plugin_root / "SKILL.md").write_text(
        "# Incident Reporter\n\nUse this tool to convert structured incident data into a concise report.\n",
        encoding="utf-8",
    )
    (plugin_root / "scripts").mkdir(parents=True, exist_ok=True)
    (plugin_root / "scripts" / "main.py").write_text(
        "import argparse\n"
        "import json\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--json', action='store_true')\n"
        "parser.add_argument('--input-file')\n"
        "args = parser.parse_args()\n"
        "payload = {}\n"
        "if args.input_file:\n"
        "    with open(args.input_file, 'r', encoding='utf-8') as handle:\n"
        "        payload = json.load(handle)\n"
        "print(json.dumps({'summary': f\"INCIDENT:{payload.get('title', 'unknown')}\", 'severity': payload.get('severity', 'unknown')}))\n",
        encoding="utf-8",
    )
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path in repo_root.rglob("*"):
            if not path.is_file():
                continue
            archive.writestr(str(path.relative_to(repo_root)).replace("\\", "/"), path.read_bytes())
    buffer.seek(0)
    return buffer


def _zip_marketplace_repo(root: Path) -> io.BytesIO:
    repo_root = root / "marketplace-repo"
    plugin_root = repo_root / "plugins" / "docs_only"
    plugin_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "Docs Only Repo",
                "plugins": [{"name": "docs_only", "source": "plugins/docs_only"}],
            }
        ),
        encoding="utf-8",
    )
    (plugin_root / "SKILL.md").write_text("# Docs Only\n", encoding="utf-8")
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path in repo_root.rglob("*"):
            if not path.is_file():
                continue
            archive.writestr(str(path.relative_to(repo_root)).replace("\\", "/"), path.read_bytes())
    buffer.seek(0)
    return buffer


def _make_temp_dir() -> Path:
    path = Path(__file__).parent / ".tmp" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    tmp_root = _make_temp_dir()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(settings, "storage_root", str(tmp_root / "storage"))
    monkeypatch.setattr(mcp_protocol, "sessions", {})
    SQLModel.metadata.create_all(engine)

    with TestClient(app) as test_client:
        yield test_client, tmp_root

    SQLModel.metadata.drop_all(engine)
    shutil.rmtree(tmp_root, ignore_errors=True)


def _register(client: TestClient, account: str, password: str = "pass123") -> str:
    response = client.post("/auth/register", json={"account": account, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workspace_id_for(client: TestClient, token: str, workspace_type: str) -> int:
    response = client.get("/workspaces", headers=_auth_headers(token))
    assert response.status_code == 200, response.text
    for workspace in response.json():
        if workspace["type"] == workspace_type:
            return workspace["id"]
    raise AssertionError(f"Workspace type not found: {workspace_type}")


def _mcp_initialize(client: TestClient, token: str, workspace_id: int) -> str:
    response = client.post(
        f"/mcp/workspaces/{workspace_id}",
        headers=_auth_headers(token),
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert response.status_code == 200, response.text
    session_id = response.headers.get("Mcp-Session-Id")
    assert session_id
    return session_id


def _mcp_request(client: TestClient, token: str, workspace_id: int, session_id: str, method: str, params: dict | None = None):
    response = client.post(
        f"/mcp/workspaces/{workspace_id}",
        headers={**_auth_headers(token), "Mcp-Session-Id": session_id},
        json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    return payload["result"]


def test_workspace_mcp_exposes_backend_tools_and_docs_for_agents(client):
    test_client, tmp_path = client
    admin_token = _register(test_client, "admin")
    member_token = _register(test_client, "member")

    create_team = test_client.post("/teams", headers=_auth_headers(admin_token), json={"name": "Ops Team"})
    assert create_team.status_code == 200, create_team.text
    team_id = create_team.json()["id"]
    team_workspace_id = _workspace_id_for(test_client, admin_token, "team")

    join_request = test_client.post("/teams/join-requests", headers=_auth_headers(member_token), json={"team_id": team_id})
    assert join_request.status_code == 200, join_request.text
    request_id = join_request.json()["id"]
    approve = test_client.post(
        f"/teams/{team_id}/join-requests/{request_id}",
        headers=_auth_headers(admin_token),
        json={"approve": True},
    )
    assert approve.status_code == 200, approve.text

    echo_zip = _zip_single_skill_package(tmp_path)
    upload_echo = test_client.post(
        f"/workspaces/{team_workspace_id}/skills/upload",
        headers=_auth_headers(admin_token),
        files={"package": ("echo-skill.zip", echo_zip.getvalue(), "application/zip")},
    )
    assert upload_echo.status_code == 200, upload_echo.text

    repo_zip = _zip_exec_repo(tmp_path)
    upload_repo = test_client.post(
        f"/workspaces/{team_workspace_id}/skills/upload",
        headers=_auth_headers(admin_token),
        files={"package": ("ops-skills.zip", repo_zip.getvalue(), "application/zip")},
    )
    assert upload_repo.status_code == 200, upload_repo.text

    availability = test_client.get(f"/workspaces/{team_workspace_id}/skill-availability", headers=_auth_headers(admin_token))
    skill_ids = [item["id"] for item in availability.json()]
    save_availability = test_client.put(
        f"/workspaces/{team_workspace_id}/skill-availability",
        headers=_auth_headers(admin_token),
        json={"enabled_skill_ids": skill_ids},
    )
    assert save_availability.status_code == 200, save_availability.text

    session_id = _mcp_initialize(test_client, member_token, team_workspace_id)
    tools_list = _mcp_request(test_client, member_token, team_workspace_id, session_id, "tools/list")
    tool_names = [tool["name"] for tool in tools_list["tools"]]
    assert "skills_list" not in tool_names
    assert "skill_call" not in tool_names
    assert any(name.startswith("skill_") and name.endswith("_echo") for name in tool_names)
    assert any(name.startswith("skill_") and name.endswith("_incident_reporter") for name in tool_names)

    resources_list = _mcp_request(test_client, member_token, team_workspace_id, session_id, "resources/list")
    resources = resources_list["resources"]
    assert any(item["name"] == "Echo Skill instructions" for item in resources)
    incident_resource = next(item for item in resources if item["name"] == "Ops Skills instructions")
    read_result = _mcp_request(
        test_client,
        member_token,
        team_workspace_id,
        session_id,
        "resources/read",
        {"uri": incident_resource["uri"]},
    )
    assert "Incident Reporter" in read_result["contents"][0]["text"]

    echo_tool = next(name for name in tool_names if name.endswith("_echo"))
    echo_result = _mcp_request(
        test_client,
        member_token,
        team_workspace_id,
        session_id,
        "tools/call",
        {"name": echo_tool, "arguments": {"text": "hello"}},
    )
    assert echo_result["isError"] is False
    assert "echo:hello" in echo_result["content"][0]["text"]

    incident_tool = next(name for name in tool_names if name.endswith("_incident_reporter"))
    incident_result = _mcp_request(
        test_client,
        member_token,
        team_workspace_id,
        session_id,
        "tools/call",
        {"name": incident_tool, "arguments": {"input": {"title": "DB outage", "severity": "high"}}},
    )
    assert incident_result["isError"] is False
    rendered = "\n".join(item["text"] for item in incident_result["content"])
    assert "INCIDENT:DB outage" in rendered
    assert '"severity": "high"' in rendered

    api_key_response = test_client.post(
        "/users/me/api-keys",
        headers=_auth_headers(member_token),
        json={"workspace_id": team_workspace_id},
    )
    assert api_key_response.status_code == 200, api_key_response.text
    api_key = api_key_response.json()["token"]
    api_session = _mcp_initialize(test_client, api_key, team_workspace_id)
    api_tools = _mcp_request(test_client, api_key, team_workspace_id, api_session, "tools/list")
    assert len(api_tools["tools"]) == len(tool_names)

    selectable = test_client.get(f"/teams/{team_id}/selectable-skills", headers=_auth_headers(member_token))
    selected_echo_id = next(item["id"] for item in selectable.json() if item["name"] == "Echo Skill")
    save_preferences = test_client.put(
        f"/teams/{team_id}/me/skills",
        headers=_auth_headers(member_token),
        json={"enabled_skill_ids": [selected_echo_id]},
    )
    assert save_preferences.status_code == 200, save_preferences.text

    filtered_session = _mcp_initialize(test_client, member_token, team_workspace_id)
    filtered_tools = _mcp_request(test_client, member_token, team_workspace_id, filtered_session, "tools/list")
    filtered_names = [tool["name"] for tool in filtered_tools["tools"]]
    assert any(name.endswith("_echo") for name in filtered_names)
    assert not any(name.endswith("_incident_reporter") for name in filtered_names)


def test_docs_only_marketplace_repo_upload_is_imported_as_docs_backed_skill(client):
    test_client, tmp_path = client
    admin_token = _register(test_client, "owner")
    personal_workspace_id = _workspace_id_for(test_client, admin_token, "personal")

    marketplace_zip = _zip_marketplace_repo(tmp_path)
    response = test_client.post(
        f"/workspaces/{personal_workspace_id}/skills/upload",
        headers=_auth_headers(admin_token),
        files={"package": ("docs-only.zip", marketplace_zip.getvalue(), "application/zip")},
    )
    assert response.status_code == 200, response.text
    skill_id = response.json()["id"]

    session_id = _mcp_initialize(test_client, admin_token, personal_workspace_id)
    tools_list = _mcp_request(test_client, admin_token, personal_workspace_id, session_id, "tools/list")
    tool_name = next(tool["name"] for tool in tools_list["tools"] if tool["name"].endswith("_docs_only"))

    resources = _mcp_request(test_client, admin_token, personal_workspace_id, session_id, "resources/list")["resources"]
    resource = next(item for item in resources if item["name"] == "Docs Only Repo instructions")
    read_result = _mcp_request(
        test_client,
        admin_token,
        personal_workspace_id,
        session_id,
        "resources/read",
        {"uri": resource["uri"]},
    )
    assert "Docs Only" in read_result["contents"][0]["text"]

    call_result = _mcp_request(
        test_client,
        admin_token,
        personal_workspace_id,
        session_id,
        "tools/call",
        {"name": tool_name, "arguments": {}},
    )
    assert call_result["isError"] is False
    assert "Docs Only" in call_result["content"][0]["text"]
    assert '"mode": "docs_only"' in call_result["content"][1]["text"]
