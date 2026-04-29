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

import app.database as database_module
from app.config import settings
from app.main import app


def _make_temp_dir() -> Path:
    path = Path(__file__).parent / ".tmp" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def _zip_example_skill() -> bytes:
    root = Path(__file__).resolve().parents[2] / "examples" / "server-transfer-skill"
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            archive.writestr(str(path.relative_to(root)).replace("\\", "/"), path.read_bytes())
    buffer.seek(0)
    return buffer.getvalue()


def _zip_example_skill_without_version() -> bytes:
    root = Path(__file__).resolve().parents[2] / "examples" / "server-transfer-skill"
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = str(path.relative_to(root)).replace("\\", "/")
            if relative == "skill.json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload.pop("version", None)
                archive.writestr(relative, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
            else:
                archive.writestr(relative, path.read_bytes())
    buffer.seek(0)
    return buffer.getvalue()


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


def test_super_admin_review_workbench_prepares_download_and_deployment_flow(client):
    test_client, _tmp_root = client
    super_admin_token = _register(test_client, "root")
    admin_token = _register(test_client, "team-admin")
    member_token = _register(test_client, "team-member")

    create_team = test_client.post("/teams", headers=_auth_headers(admin_token), json={"name": "Delivery Team"})
    assert create_team.status_code == 200, create_team.text
    team_id = create_team.json()["id"]
    team_workspace_id = _workspace_id_for(test_client, admin_token, "team")

    join_request = test_client.post("/teams/join-requests", headers=_auth_headers(member_token), json={"team_id": team_id})
    assert join_request.status_code == 200, join_request.text
    approve_join = test_client.post(
        f"/teams/{team_id}/join-requests/{join_request.json()['id']}",
        headers=_auth_headers(admin_token),
        json={"approve": True},
    )
    assert approve_join.status_code == 200, approve_join.text

    upload = test_client.post(
        f"/workspaces/{team_workspace_id}/skills/upload",
        headers=_auth_headers(member_token),
        files={"package": ("server-transfer-skill.zip", _zip_example_skill(), "application/zip")},
    )
    assert upload.status_code == 200, upload.text
    skill_id = upload.json()["id"]
    assert upload.json()["current_approved_version_id"] is None

    versions = test_client.get(f"/skills/{skill_id}/versions", headers=_auth_headers(member_token))
    assert versions.status_code == 200, versions.text
    version_id = versions.json()[0]["id"]
    member_download = test_client.get(versions.json()[0]["package_download_url"], headers=_auth_headers(member_token))
    assert member_download.status_code == 200, member_download.text
    assert member_download.content[:2] == b"PK"

    review_queue = test_client.get("/review/skill-versions", headers=_auth_headers(super_admin_token))
    assert review_queue.status_code == 200, review_queue.text
    assert any(item["id"] == version_id for item in review_queue.json())

    start_review = test_client.post(f"/skill-versions/{version_id}/start-review", headers=_auth_headers(super_admin_token))
    assert start_review.status_code == 200, start_review.text
    workbench = start_review.json()
    assert workbench["deployment_ready"] is True
    assert workbench["deployment_kind"] == "python_package"
    assert workbench["tool_count"] == 3
    assert Path(workbench["workbench_path"]).exists()
    assert Path(workbench["workbench_package_path"]).exists()
    assert Path(workbench["workbench_extracted_path"]).exists()
    assert workbench["version"]["package_download_url"] == f"/skill-versions/{version_id}/download"
    assert workbench["version"]["skill_name"] == "Server Transfer Skill"

    download = test_client.get(workbench["version"]["package_download_url"], headers=_auth_headers(super_admin_token))
    assert download.status_code == 200, download.text
    assert download.content[:2] == b"PK"

    approve = test_client.post(f"/skill-versions/{version_id}/approve", headers=_auth_headers(super_admin_token))
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "approved"
    assert approve.json()["is_current_approved"] is True

    approved_before_deploy = test_client.get(
        f"/workspaces/{team_workspace_id}/approved-skills",
        headers=_auth_headers(member_token),
    )
    assert approved_before_deploy.status_code == 200, approved_before_deploy.text
    assert approved_before_deploy.json() == []

    deploy = test_client.post(f"/skill-versions/{version_id}/deploy", headers=_auth_headers(super_admin_token))
    assert deploy.status_code == 200, deploy.text
    assert deploy.json()["is_current_deployed"] is True
    assert deploy.json()["deployment_path"]
    assert deploy.json()["published_mcp_endpoint_url"] == f"http://testserver/mcp/{team_workspace_id}/{skill_id}"

    approved_before_exposure = test_client.get(
        f"/workspaces/{team_workspace_id}/approved-skills",
        headers=_auth_headers(member_token),
    )
    assert approved_before_exposure.status_code == 200, approved_before_exposure.text
    assert approved_before_exposure.json() == [
        {
            "skill_id": skill_id,
            "name": "Server Transfer Skill",
            "description": "Stream audio or text payloads into server-side storage and delete one or many streams.",
            "enabled": False,
            "current_approved_version_id": version_id,
            "current_approved_version": "1.0.0",
        }
    ]

    enable_exposure = test_client.put(
        f"/workspaces/{team_workspace_id}/skill-exposure",
        headers=_auth_headers(admin_token),
        json={"enabled_skill_ids": [skill_id]},
    )
    assert enable_exposure.status_code == 200, enable_exposure.text
    assert enable_exposure.json()[0]["enabled"] is True

    approved_after_exposure = test_client.get(
        f"/workspaces/{team_workspace_id}/approved-skills",
        headers=_auth_headers(member_token),
    )
    assert approved_after_exposure.status_code == 200, approved_after_exposure.text
    assert approved_after_exposure.json()[0]["enabled"] is True

    prompt = test_client.get(
        f"/workspaces/{team_workspace_id}/agent-prompt",
        headers=_auth_headers(member_token),
    )
    assert prompt.status_code == 200, prompt.text
    assert "global_upload_audio_files" in prompt.json()["prompt_text"]
    assert "global_download_processed_artifacts" in prompt.json()["prompt_text"]
    assert "Mcp-Session-Id" in prompt.json()["prompt_text"]
    assert "tools/list" in prompt.json()["prompt_text"]
    assert "resources/read" in prompt.json()["prompt_text"]
    assert prompt.json()["workspace_mcp_url"] == "deprecated"
    assert f"http://testserver/mcp/{team_workspace_id}/{skill_id}" in prompt.json()["prompt_text"]


def test_public_skill_is_selectable_and_syncable(client):
    test_client, _tmp_root = client
    super_admin_token = _register(test_client, "root")
    owner_token = _register(test_client, "owner")
    admin_token = _register(test_client, "team-admin")
    member_token = _register(test_client, "team-member")

    personal_workspace_id = _workspace_id_for(test_client, owner_token, "personal")
    create_team = test_client.post("/teams", headers=_auth_headers(admin_token), json={"name": "Public Team"})
    assert create_team.status_code == 200, create_team.text
    team_id = create_team.json()["id"]
    team_workspace_id = _workspace_id_for(test_client, admin_token, "team")

    owner_join_request = test_client.post("/teams/join-requests", headers=_auth_headers(owner_token), json={"team_id": team_id})
    assert owner_join_request.status_code == 200, owner_join_request.text
    owner_join_approve = test_client.post(
        f"/teams/{team_id}/join-requests/{owner_join_request.json()['id']}",
        headers=_auth_headers(admin_token),
        json={"approve": True},
    )
    assert owner_join_approve.status_code == 200, owner_join_approve.text

    join_request = test_client.post("/teams/join-requests", headers=_auth_headers(member_token), json={"team_id": team_id})
    assert join_request.status_code == 200, join_request.text
    approve_join = test_client.post(
        f"/teams/{team_id}/join-requests/{join_request.json()['id']}",
        headers=_auth_headers(admin_token),
        json={"approve": True},
    )
    assert approve_join.status_code == 200, approve_join.text

    upload = test_client.post(
        f"/workspaces/{personal_workspace_id}/skills/upload",
        headers=_auth_headers(owner_token),
        data={"visibility": "public"},
        files={"package": ("server-transfer-skill.zip", _zip_example_skill(), "application/zip")},
    )
    assert upload.status_code == 200, upload.text
    skill_id = upload.json()["id"]
    assert upload.json()["visibility"] == "public"

    versions = test_client.get(f"/skills/{skill_id}/versions", headers=_auth_headers(owner_token))
    assert versions.status_code == 200, versions.text
    version_id = versions.json()[0]["id"]
    approve = test_client.post(f"/skill-versions/{version_id}/approve", headers=_auth_headers(super_admin_token))
    assert approve.status_code == 200, approve.text
    deploy = test_client.post(f"/skill-versions/{version_id}/deploy", headers=_auth_headers(super_admin_token))
    assert deploy.status_code == 200, deploy.text

    public_initialize = test_client.post(
        f"/mcp/{personal_workspace_id}/{skill_id}",
        headers=_auth_headers(member_token),
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert public_initialize.status_code == 200, public_initialize.text
    public_session_id = public_initialize.headers["Mcp-Session-Id"]
    public_tools = test_client.post(
        f"/mcp/{personal_workspace_id}/{skill_id}",
        headers={**_auth_headers(member_token), "Mcp-Session-Id": public_session_id},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert public_tools.status_code == 200, public_tools.text
    assert public_tools.json()["result"]["tools"]

    selectable = test_client.get(f"/teams/{team_id}/selectable-skills", headers=_auth_headers(member_token))
    assert selectable.status_code == 200, selectable.text
    assert any(item["id"] == skill_id and item["source"] == "public" for item in selectable.json())

    prefs = test_client.put(
        f"/teams/{team_id}/me/skills",
        headers=_auth_headers(member_token),
        json={"enabled_skill_ids": [skill_id]},
    )
    assert prefs.status_code == 200, prefs.text
    assert prefs.json()["configured"] is True

    sync = test_client.post(
        f"/skill-versions/{version_id}/sync-to-workspace",
        headers=_auth_headers(owner_token),
        json={"target_workspace_id": team_workspace_id, "visibility": "private"},
    )
    assert sync.status_code == 200, sync.text
    assert sync.json()["workspace_id"] == team_workspace_id
    assert sync.json()["visibility"] == "private"


def test_workspace_mcp_is_deprecated(client):
    test_client, _tmp_root = client
    token = _register(test_client, "owner")
    workspace_id = _workspace_id_for(test_client, token, "personal")

    initialize = test_client.post(
        f"/mcp/workspaces/{workspace_id}",
        headers=_auth_headers(token),
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert initialize.status_code == 410, initialize.text
    assert initialize.json()["error"]["code"] == -32004
    assert "deprecated" in initialize.json()["error"]["message"].lower()


def test_upload_allows_manual_version_when_manifest_has_no_version(client):
    test_client, _tmp_root = client
    owner_token = _register(test_client, "owner")
    personal_workspace_id = _workspace_id_for(test_client, owner_token, "personal")

    upload = test_client.post(
        f"/workspaces/{personal_workspace_id}/skills/upload",
        headers=_auth_headers(owner_token),
        data={"version": "manual-v1"},
        files={"package": ("server-transfer-skill.zip", _zip_example_skill_without_version(), "application/zip")},
    )
    assert upload.status_code == 200, upload.text
    skill_id = upload.json()["id"]

    versions = test_client.get(f"/skills/{skill_id}/versions", headers=_auth_headers(owner_token))
    assert versions.status_code == 200, versions.text
    assert versions.json()[0]["version"] == "manual-v1"
