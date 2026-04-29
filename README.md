# SkillHub

Open-source Skill management console and MCP gateway for personal Skills, public Skill Market, and super-admin deployment.

[English](README.md) | [简体中文](README.zh-CN.md)

## Overview

SkillHub lets users upload Skill ZIP packages, lets super admins review and deploy them, and exposes approved runtime Skills as concrete MCP endpoints that agents can call.

The current product model is intentionally simple:

- Users manage their own Skills in a personal workspace.
- Users can add public Skills from Skill Market into their own working list.
- Super admins review packages, deploy runtimes, open MCP endpoints, configure prompts, and manage Market Skills.
- Team/workspace compatibility remains in the backend, but the main UI flow focuses on personal Skills and the public Market.

## Features

- Login/register with bearer-token authentication.
- Personal Skill upload and version tracking.
- Public Skill Market with details, tools, ZIP download, and prompt copy.
- Super-admin Deploy Workbench for review, deploy, open MCP, reject, and prompt configuration.
- Concrete Skill MCP endpoint: `/mcp/{workspace_id}/{skill_id}`.
- Generated agent prompts that include MCP connection steps, authentication, Skill usage instructions, and global artifact transfer tools.
- Global MCP tools for audio/text upload, processed artifact download, and cleanup.
- Optional cleanup script for temporary audio/archive artifacts.

## Repository Layout

```text
skillhub/
|-- backend/
|   |-- app/
|   |-- alembic/
|   |-- cleanup_audio_artifacts.py
|   `-- pyproject.toml
|-- examples/
|   |-- echo-skill/
|   `-- server-transfer-skill/
|-- frontend/
|   |-- src/
|   `-- package.json
|-- scripts/
|-- .env.example
|-- README.md
`-- README.zh-CN.md
```

Runtime files such as `.env`, SQLite databases, logs, storage directories, ZIP packages, frontend build output, and Codex/server sync artifacts are intentionally ignored by Git.

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js and npm
- PowerShell if using the helper script on Windows

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
npm run build
```

### Configuration

Copy `.env.example` to `backend/.env` for local development and replace every placeholder:

```env
DATABASE_URL=sqlite:///./data/skillhub.db
SECRET_KEY=<generate-a-long-random-secret>
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ALGORITHM=HS256
FRONTEND_URL=http://localhost:5173
STORAGE_ROOT=./storage
SUPER_ADMIN_ACCOUNT=<admin-account>
SUPER_ADMIN_PASSWORD=<admin-password>
VITE_API_BASE_URL=/api
```

Do not commit real `.env` files, database files, logs, uploaded packages, storage folders, or generated deployment artifacts.

### Run Both Services

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-skillhub.ps1
```

Default local URLs:

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8000`
- OpenAPI: `http://127.0.0.1:8000/docs`

## Skill Package Rules

- Upload endpoint: `POST /workspaces/{workspace_id}/skills/upload`
- ZIP packages should include `skill.json` or `skillhub.json`.
- A docs-only Skill can also be imported from a package containing `SKILL.md`.
- A repository package can expose multiple executable Skills through `skills-index.json` or marketplace metadata.
- Uploading only creates an uploaded version. It is not MCP-ready until a super admin deploys and approves it.

## Super Admin Flow

1. User uploads a Skill ZIP.
2. Super admin starts review with `POST /skill-versions/{version_id}/start-review`.
3. Super admin deploys the version.
4. Runtime deployment copies the reviewed package into backend storage and creates a per-version virtual environment.
5. Dependencies are installed from `requirements.txt`, `pyproject.toml`, or runtime metadata.
6. Super admin approves the version and opens the concrete MCP endpoint.
7. Super admin can edit Skill-specific prompt content and prompt join logic.

## MCP Usage

Use only concrete Skill MCP endpoints:

```text
/mcp/{workspace_id}/{skill_id}
```

The deprecated workspace MCP endpoint returns an explicit error and should not be used for tool calls.

Runtime visibility requires:

- approved version
- deployed runtime
- published MCP endpoint URL
- valid `Authorization: Bearer <access_token>`

Generated prompts include the full MCP call sequence:

1. Send `initialize` to the concrete MCP endpoint with the Authorization header.
2. Read the `Mcp-Session-Id` response header and include it on later MCP requests.
3. Call `tools/list` before selecting a business tool.
4. Call `resources/list` and `resources/read` when resources are present.
5. Call `tools/call` with `params.name` and JSON `params.arguments`.
6. For file/audio work, upload one artifact, call one business tool, download the output, delete input/output artifacts, then continue.

## Global Artifact Tools

Every concrete Skill MCP endpoint exposes these helper tools:

- `global_upload_audio_files`
- `global_upload_text_files`
- `global_download_processed_artifacts`
- `global_download_processed_artifacts_and_cleanup`
- `global_delete_uploaded_artifacts`

The transfer tools are streaming-oriented:

- upload one file per call
- process one input artifact per business-tool call
- download one processed artifact per call
- delete one artifact per call

Bulk artifact IDs are rejected for upload, processing, and normal download/delete paths so agents do not create long-running opaque jobs.

## Artifact HTTP APIs

- `POST /artifacts/audio`: upload one local audio file with multipart field `file`
- `GET /artifacts/{artifact_id}`: read artifact manifest
- `GET /artifacts/{artifact_id}/download`: download one artifact
- `DELETE /artifacts/{artifact_id}?mode=soft|hard`: delete one artifact

Artifacts are stored under the configured `STORAGE_ROOT`. Artifact IDs are validated and path traversal is blocked.

## Cleanup

Temporary audio/archive artifacts can be cleaned with:

```bash
python backend/cleanup_audio_artifacts.py --older-than-hours 24 --mode hard
```

The cleanup script only scans SkillHub artifact storage and does not delete Skill source packages, deployed runtimes, databases, logs, or unrelated server files.

## Tests

Backend tests:

```powershell
.\.venv\Scripts\pytest.exe backend\tests -q
```

Frontend build:

```powershell
cd frontend
npm run build
```

## Security Notes

- Treat uploaded Skill packages as untrusted until reviewed.
- Use a strong `SECRET_KEY` in every real deployment.
- Keep `SUPER_ADMIN_ACCOUNT` and `SUPER_ADMIN_PASSWORD` outside Git.
- Do not commit `.env`, `backend/data`, `backend/storage`, logs, uploaded ZIPs, generated frontend `dist`, or remote sync artifacts.
- Rotate any credential that was ever written into a local scratch file before publishing the repository.

## Example Skill Packages

- `examples/echo-skill`
- `examples/server-transfer-skill`
