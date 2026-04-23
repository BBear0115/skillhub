# SkillHub

Open-source skill management and MCP gateway for personal and team workspaces.

[English](README.md) | [简体中文](README.zh-CN.md)

SkillHub now uses a three-layer control model:

- Super admin reviews `SkillVersion` and decides whether a version may enter MCP.
- Team admins choose which approved `Skill` entries are exposed inside their team workspace.
- Any team member may upload ZIP packages into the team workspace and create candidate versions.

Super admins can also start a review workbench for a candidate version. That prepares a deploy-ready server-side workbench directory, keeps the ZIP snapshot available for download, and exposes deployment metadata before approval.

Actual MCP visibility requires both conditions:

- The skill has a `current_approved_version_id`
- The team workspace exposure for that skill is enabled

## Core Concepts

- `Skill`: stable identity in a workspace
- `SkillVersion`: immutable ZIP-backed version snapshot with `uploaded`, `approved`, `rejected`, or `archived` status
- `WorkspaceSkillExposure`: team-level switch that controls whether an approved skill is exposed to team MCP

Uploading a ZIP does not expose it to MCP. It only creates a candidate version.

## Repository Layout

```text
skillhub/
|-- backend/
|   |-- app/
|   |-- alembic/
|   `-- pyproject.toml
|-- frontend/
|   |-- public/
|   |-- src/
|   `-- package.json
|-- examples/
|-- scripts/
|-- .env.example
|-- README.md
`-- README.zh-CN.md
```

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js and npm
- PowerShell if you want to use the helper script

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

### Frontend

```powershell
cd frontend
npm install
```

### Run both services

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-skillhub.ps1
```

Default URLs:

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8000`
- OpenAPI: `http://127.0.0.1:8000/docs`

Local persistence:

- SQLite metadata: `backend/data/skillhub.db`
- Uploaded ZIPs and extracted packages: `backend/storage/`

## Environment Variables

Reference values from [.env.example](.env.example):

```env
DATABASE_URL=sqlite:///./data/skillhub.db
SECRET_KEY=dev-secret-key-change-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ALGORITHM=HS256
FRONTEND_URL=http://localhost:5173
STORAGE_ROOT=./storage
SUPER_ADMIN_ACCOUNT=
VITE_API_BASE_URL=/api
```

Set `SUPER_ADMIN_ACCOUNT` to the account name that should be allowed to approve or reject skill versions.

## ZIP Upload Rules

- ZIP manifest must include `name`
- ZIP manifest must include `version`
- Upload endpoint: `POST /workspaces/{workspace_id}/skills/upload`
- Implicit skill packages that only ship `SKILL.md + scripts/` may not include a manifest version. In that case, provide `version` in the multipart form when uploading.

An upload creates:

- a new `Skill` plus one `uploaded` version, or
- a new `uploaded` version under an existing skill with the same manifest name

## Review Workbench

- `POST /skill-versions/{version_id}/start-review`
- Prepares a review workbench directory under backend storage
- Copies the uploaded ZIP snapshot and extracted package contents into that workbench
- Returns deployment metadata, handler details, and download information for the super admin

## Main API Surface

- `GET /workspaces/{workspace_id}/skills`
- `GET /skills/{skill_id}`
- `GET /skills/{skill_id}/versions`
- `GET /skill-versions/{version_id}`
- `GET /skill-versions/{version_id}/download`
- `POST /skill-versions/{version_id}/approve`
- `POST /skill-versions/{version_id}/reject`
- `POST /skills/{skill_id}/clear-approved-version`
- `GET /workspaces/{workspace_id}/approved-skills`
- `GET /workspaces/{workspace_id}/skill-exposure`
- `PUT /workspaces/{workspace_id}/skill-exposure`

## MCP Endpoints

Skill-level endpoint:

```text
/mcp/{workspace_id}/{skill_id}
```

Only skill-level MCP is exposed for tool calls. It reads the current approved and deployed version snapshot. Unapproved, rejected, archived, undeployed, or non-exposed versions are invisible to MCP.

The legacy workspace MCP endpoint `/mcp/workspaces/{workspace_id}` is deprecated and returns an explicit error. It no longer mixes global tools, skill discovery, nested dispatch, or alias tools into one MCP surface.

## Super Admin Runtime Workspace

Super admins have a dedicated admin workspace for review and deployment. Deploying a skill version:

- copies the reviewed package into `storage/admin-workspaces/.../deployments/...`
- creates a per-version `.venv`
- installs dependencies from `requirements.txt`, `pyproject.toml`, or optional `runtime.dependencies` in the skill manifest
- publishes the skill MCP URL only if deployment succeeds

## Agent Notes

- Super-admin review and deploy flows publish a concrete skill MCP endpoint: `server_base_url + mcp_endpoint`.
- If a package has no embedded version, agents should generate and submit a deterministic form `version` during upload.
- Agents should use concrete skill MCP URLs. Workspace MCP is deprecated.
- Agent access uses standard `Authorization: Bearer <access_token>` only. API-key based MCP access has been removed.

## Example Skill Packages

- `examples/echo-skill`
- `examples/server-transfer-skill`

`server-transfer-skill` includes:

- `stream_audio_to_server`
- `stream_text_to_server`
- `delete_server_streams`
