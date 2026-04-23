import base64
import io
import json
import math
import shutil
import wave
from pathlib import Path
from subprocess import CompletedProcess
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import app.database as database_module
from app.config import settings
from app.main import app


def _make_temp_dir() -> Path:
    path = Path(__file__).parent / ".tmp" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dnsmos_zip_path() -> Path:
    return Path(__file__).resolve().parents[2] / "dnsmos-audio-filter (1).zip"


def _dnsmos_zip_bytes() -> bytes:
    return _dnsmos_zip_path().read_bytes()


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
    monkeypatch.setattr(settings, "super_admin_account", "root")
    monkeypatch.setattr(settings, "super_admin_password", "pass123")
    SQLModel.metadata.create_all(engine)

    with TestClient(app) as test_client:
        yield test_client, tmp_root

    SQLModel.metadata.drop_all(engine)
    shutil.rmtree(tmp_root, ignore_errors=True)


def _register(client: TestClient, account: str, password: str = "pass123") -> str:
    response = client.post("/auth/register", json={"account": account, "password": password})
    if response.status_code == 400 and response.json().get("detail") == "Account already registered":
        response = client.post("/auth/login", json={"account": account, "password": password})
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


def _create_team_with_member(client: TestClient, admin_token: str, member_token: str, team_name: str) -> tuple[int, int]:
    create_team = client.post("/teams", headers=_auth_headers(admin_token), json={"name": team_name})
    assert create_team.status_code == 200, create_team.text
    team_id = create_team.json()["id"]
    workspace_id = _workspace_id_for(client, admin_token, "team")

    join_request = client.post("/teams/join-requests", headers=_auth_headers(member_token), json={"team_id": team_id})
    assert join_request.status_code == 200, join_request.text
    approve_join = client.post(
        f"/teams/{team_id}/join-requests/{join_request.json()['id']}",
        headers=_auth_headers(admin_token),
        json={"approve": True},
    )
    assert approve_join.status_code == 200, approve_join.text
    return team_id, workspace_id


def _jsonrpc(client: TestClient, endpoint: str, token: str, session_id: str | None, method: str, params: dict, req_id: int = 1) -> dict:
    headers = _auth_headers(token)
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    response = client.post(
        endpoint,
        headers=headers,
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    return payload


def _create_wav_file(target: Path, seconds: float = 0.1, sample_rate: int = 16000) -> bytes:
    frames = int(seconds * sample_rate)
    amplitude = 8000
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        samples = bytearray()
        for index in range(frames):
            sample = int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate))
            samples.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav_file.writeframes(bytes(samples))
    data = buffer.getvalue()
    target.write_bytes(data)
    return data


def _extract_produced_artifacts(tool_result: dict) -> list[dict]:
    for item in tool_result["content"]:
        if item.get("type") != "text":
            continue
        try:
            payload = json.loads(item["text"])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("produced_artifacts"), list):
            return payload["produced_artifacts"]
    return []


class SimulatedSuperAdminReviewAgent:
    def __init__(self, client: TestClient, token: str):
        self.client = client
        self.token = token

    def infer_upload_version(self, package_path: Path) -> str:
        stem = package_path.stem.replace(" ", "-").replace("(", "").replace(")", "")
        return f"{stem}-agent-v1"

    def upload_dnsmos_skill(self, workspace_id: int, uploader_token: str, package_path: Path) -> dict:
        response = self.client.post(
            f"/workspaces/{workspace_id}/skills/upload",
            headers=_auth_headers(uploader_token),
            data={"version": self.infer_upload_version(package_path)},
            files={"package": (package_path.name, package_path.read_bytes(), "application/zip")},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def list_version(self, skill_id: int, uploader_token: str) -> dict:
        response = self.client.get(f"/skills/{skill_id}/versions", headers=_auth_headers(uploader_token))
        assert response.status_code == 200, response.text
        versions = response.json()
        assert len(versions) == 1
        return versions[0]

    def start_review(self, version_id: int) -> dict:
        response = self.client.post(f"/skill-versions/{version_id}/start-review", headers=_auth_headers(self.token))
        assert response.status_code == 200, response.text
        return response.json()

    def approve(self, version_id: int) -> dict:
        response = self.client.post(f"/skill-versions/{version_id}/approve", headers=_auth_headers(self.token))
        assert response.status_code == 200, response.text
        return response.json()

    def deploy(self, version_id: int, review_attempt_id: str) -> dict:
        response = self.client.post(
            f"/skill-versions/{version_id}/deploy",
            headers=_auth_headers(self.token),
            json={"review_attempt_id": review_attempt_id},
        )
        assert response.status_code == 200, response.text
        return response.json()


class SimulatedWorkspaceAgent:
    def __init__(self, client: TestClient, token: str, workspace_id: int):
        self.client = client
        self.token = token
        self.workspace_id = workspace_id
        self.endpoint = f"/mcp/workspaces/{workspace_id}"
        self.session_id: str | None = None

    def initialize(self) -> None:
        response = self.client.post(
            self.endpoint,
            headers=_auth_headers(self.token),
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert response.status_code == 200, response.text
        self.session_id = response.headers["Mcp-Session-Id"]

    def tools_list(self) -> list[dict]:
        payload = _jsonrpc(self.client, self.endpoint, self.token, self.session_id, "tools/list", {}, req_id=2)
        return payload["result"]["tools"]

    def resources_list(self) -> list[dict]:
        payload = _jsonrpc(self.client, self.endpoint, self.token, self.session_id, "resources/list", {}, req_id=3)
        return payload["result"]["resources"]

    def resource_read(self, uri: str) -> str:
        payload = _jsonrpc(self.client, self.endpoint, self.token, self.session_id, "resources/read", {"uri": uri}, req_id=4)
        return payload["result"]["contents"][0]["text"]

    def call_tool(self, name: str, arguments: dict, req_id: int = 5) -> dict:
        payload = _jsonrpc(
            self.client,
            self.endpoint,
            self.token,
            self.session_id,
            "tools/call",
            {"name": name, "arguments": arguments},
            req_id=req_id,
        )
        return payload["result"]

    def discover_dnsmos(self) -> dict:
        skills_result = self.call_tool("skills_list", {}, req_id=6)
        skills_payload = json.loads(skills_result["content"][0]["text"])
        candidates = [item for item in skills_payload["skills"] if "dnsmos" in item["skill_name"].lower()]
        assert len(candidates) == 1
        return candidates[0]

    def infer_dnsmos_arguments(self, resource_text: str, input_dir: Path | None = None, input_artifact_ids: list[str] | None = None) -> dict:
        threshold = 1.3
        workers = 1
        for line in resource_text.splitlines():
            normalized = line.strip().lower()
            if normalized.startswith("- default:"):
                if threshold == 1.3 and "1.3" in normalized:
                    threshold = 1.3
            if "workers" in normalized and workers == 1:
                workers = 1
        arguments: dict[str, object] = {
            "options": {
                "model-path": "assets/sig_bak_ovr.onnx",
                "threshold": threshold,
                "workers": workers,
            }
        }
        if input_dir is not None:
            arguments["options"]["input-dir"] = str(input_dir)
            arguments["options"]["output-dir"] = str(input_dir.parent / f"{input_dir.name}_dnsmos")
        if input_artifact_ids:
            arguments["input_artifact_ids"] = list(input_artifact_ids)
        return arguments


class SimulatedSkillMcpAgent:
    def __init__(self, client: TestClient, token: str, published_url: str):
        self.client = client
        self.token = token
        self.published_url = published_url
        parsed = urlparse(published_url)
        self.endpoint = parsed.path or published_url
        self.session_id: str | None = None

    def initialize(self) -> None:
        response = self.client.post(
            self.endpoint,
            headers=_auth_headers(self.token),
            json={"jsonrpc": "2.0", "id": 101, "method": "initialize", "params": {}},
        )
        assert response.status_code == 200, response.text
        self.session_id = response.headers["Mcp-Session-Id"]

    def tools_list(self) -> list[dict]:
        payload = _jsonrpc(self.client, self.endpoint, self.token, self.session_id, "tools/list", {}, req_id=102)
        return payload["result"]["tools"]

    def resources_list(self) -> list[dict]:
        payload = _jsonrpc(self.client, self.endpoint, self.token, self.session_id, "resources/list", {}, req_id=103)
        return payload["result"]["resources"]

    def resource_read(self, uri: str) -> str:
        payload = _jsonrpc(self.client, self.endpoint, self.token, self.session_id, "resources/read", {"uri": uri}, req_id=104)
        return payload["result"]["contents"][0]["text"]

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        payload = _jsonrpc(
            self.client,
            self.endpoint,
            self.token,
            self.session_id,
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            req_id=105,
        )
        return payload["result"]


def _review_and_publish_dnsmos_skill(
    client: TestClient,
    super_admin_token: str,
    admin_token: str,
    member_token: str,
    workspace_id: int,
) -> dict:
    super_admin_agent = SimulatedSuperAdminReviewAgent(client, super_admin_token)
    package_path = _dnsmos_zip_path()
    uploaded_skill = super_admin_agent.upload_dnsmos_skill(workspace_id, member_token, package_path)
    skill_id = uploaded_skill["id"]

    version = super_admin_agent.list_version(skill_id, member_token)
    version_id = version["id"]
    workbench = super_admin_agent.start_review(version_id)
    approved = super_admin_agent.approve(version_id)
    deployed = super_admin_agent.deploy(version_id, workbench["review_attempt_id"])
    published_url = deployed["published_mcp_endpoint_url"]

    exposure = client.put(
        f"/workspaces/{workspace_id}/skill-exposure",
        headers=_auth_headers(admin_token),
        json={"enabled_skill_ids": [skill_id]},
    )
    assert exposure.status_code == 200, exposure.text

    return {
        "skill_id": skill_id,
        "version_id": version_id,
        "workbench": workbench,
        "approved": approved,
        "deployed": deployed,
        "published_url": published_url,
    }


def test_skill_mcp_exposes_dnsmos_resources_after_review_flow(client):
    test_client, _tmp_root = client
    super_admin_token = _register(test_client, "root")
    admin_token = _register(test_client, "team-admin")
    member_token = _register(test_client, "team-member")
    _team_id, workspace_id = _create_team_with_member(test_client, admin_token, member_token, "Audio QA Team")

    deployed = _review_and_publish_dnsmos_skill(test_client, super_admin_token, admin_token, member_token, workspace_id)
    skill_id = deployed["skill_id"]

    prompt = test_client.get(f"/workspaces/{workspace_id}/agent-prompt", headers=_auth_headers(member_token))
    assert prompt.status_code == 200, prompt.text
    prompt_payload = prompt.json()
    assert prompt_payload["workspace_mcp_url"] == "deprecated"
    assert "DNSMOS Audio Filter" in prompt_payload["prompt_text"]
    assert "Authentication: use Authorization: Bearer <workspace access token>." in prompt_payload["prompt_text"]
    assert "API key" not in prompt_payload["prompt_text"]
    assert "global_delete_uploaded_artifacts" not in prompt_payload["prompt_text"]
    assert deployed["published_url"] in prompt_payload["prompt_text"]

    workspace_mcp = test_client.post(
        f"/mcp/workspaces/{workspace_id}",
        headers=_auth_headers(member_token),
        json={"jsonrpc": "2.0", "id": 99, "method": "initialize", "params": {}},
    )
    assert workspace_mcp.status_code == 410
    assert workspace_mcp.json()["error"]["code"] == -32004

    agent = SimulatedSkillMcpAgent(test_client, member_token, deployed["published_url"])
    agent.initialize()
    tools = agent.tools_list()
    tool_names = {item["name"] for item in tools}
    assert tool_names == {"DNSMOS Audio Filter"}

    resources = agent.resources_list()
    assert len(resources) == 1
    assert resources[0]["uri"].endswith("/docs/dnsmos_audio_filter")
    resource_text = agent.resource_read(resources[0]["uri"])
    assert "python scripts/dnsmos_batch_filter.py" in resource_text
    assert "assets/sig_bak_ovr.onnx" in resource_text

    workbench = deployed["workbench"]
    assert workbench["deployment_kind"] == "marketplace_repo"
    assert workbench["tool_count"] == 1
    assert workbench["handler_config"]["type"] == "marketplace_repo"
    assert deployed["published_url"].endswith(f"/mcp/{workspace_id}/{skill_id}")
    assert deployed["deployed"]["published_mcp_endpoint_url"] == deployed["published_url"]
    assert deployed["deployed"]["published_mcp_endpoint_url"] == f"http://testserver/mcp/{workspace_id}/{skill_id}"
    assert deployed["approved"]["status"] == "approved"


def test_agents_complete_review_then_skill_mcp_call_dnsmos_with_agent_filled_inputs(
    client,
    monkeypatch: pytest.MonkeyPatch,
):
    test_client, tmp_root = client
    super_admin_token = _register(test_client, "root")
    admin_token = _register(test_client, "team-admin")
    member_token = _register(test_client, "team-member")
    _team_id, workspace_id = _create_team_with_member(test_client, admin_token, member_token, "Audio Agents")
    deployed = _review_and_publish_dnsmos_skill(test_client, super_admin_token, admin_token, member_token, workspace_id)

    audio_dir = tmp_root / "audio-input"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav_path = audio_dir / "sample.wav"
    wav_bytes = _create_wav_file(wav_path)

    recorded_commands: list[dict] = []

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None):
        output_dir = Path(command[command.index("--output-dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "dnsmos_scores.csv").write_text("file,OVRL,SIG,BAK\nsample.wav,3.1,3.0,2.8\n", encoding="utf-8")
        high_quality = output_dir / "high_quality"
        high_quality.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wav_path, high_quality / wav_path.name)
        recorded_commands.append(
            {
                "command": command,
                "cwd": cwd,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
            }
        )
        return CompletedProcess(
            command,
            0,
            stdout=json.dumps({"ok": True, "scanned": [str(wav_path)], "output_dir": str(audio_dir.parent / "audio-input_dnsmos")}),
            stderr="",
        )

    monkeypatch.setattr("app.services.skill_runner.subprocess.run", fake_run)

    skill_agent = SimulatedSkillMcpAgent(test_client, member_token, deployed["published_url"])
    skill_agent.initialize()
    skill_tools = skill_agent.tools_list()
    assert [tool["name"] for tool in skill_tools] == ["DNSMOS Audio Filter"]
    skill_resources = skill_agent.resources_list()
    assert len(skill_resources) == 1
    resource_text = skill_agent.resource_read(skill_resources[0]["uri"])
    assert "python scripts/dnsmos_batch_filter.py" in resource_text
    call_args = {
        "options": {
            "model-path": "assets/sig_bak_ovr.onnx",
            "threshold": 1.3,
            "workers": 1,
            "input-dir": str(audio_dir),
            "output-dir": str(audio_dir.parent / "audio-input_dnsmos"),
        }
    }
    assert call_args["options"]["model-path"] == "assets/sig_bak_ovr.onnx"

    direct_skill_result = skill_agent.call_tool("DNSMOS Audio Filter", call_args)
    assert direct_skill_result["isError"] is False

    assert len(recorded_commands) == 1
    for invocation in recorded_commands:
        command = invocation["command"]
        assert "--input-dir" in command
        resolved_input_dir = Path(command[command.index("--input-dir") + 1])
        assert resolved_input_dir.exists()
        assert (resolved_input_dir / wav_path.name).exists()
        assert str(audio_dir) in command
        assert "--model-path" in command
        assert "assets/sig_bak_ovr.onnx" in command
        assert "--output-dir" in command
        assert invocation["cwd"] is not None
