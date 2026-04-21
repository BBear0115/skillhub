# SkillHub

Open-source skill management and MCP gateway for individuals and teams.

[English](README.md) | [简体中文](README.zh-CN.md)

SkillHub helps teams organize skills, control who can manage them, and expose them to agents through MCP-compatible endpoints. This repository now uses a normal local frontend/backend setup:

- Backend: FastAPI + SQLModel + Uvicorn
- Frontend: React + Vite
- Local persistence: SQLite by default

## Core Features

- Personal and team workspaces
- ZIP skill package upload
- Team approval and skill exposure controls
- Workspace-scoped API keys
- MCP endpoints for single-skill and workspace-level access

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
- Windows PowerShell if you want to use the helper script

### Backend setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

### Frontend setup

```powershell
cd frontend
npm install
```

### Run both services

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-skillhub.ps1
```

Default local URLs:

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8000`
- OpenAPI docs: `http://127.0.0.1:8000/docs`

Persistent local data:

- Accounts and metadata: `backend/data/skillhub.db`
- Uploaded skill packages and extracted files: `backend/storage/`

### Run manually

Backend:

```powershell
cd backend
$env:DATABASE_URL="sqlite:///./data/skillhub.db"
$env:FRONTEND_URL="http://localhost:5173"
$env:STORAGE_ROOT="./storage"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

The Vite dev server proxies `/api` requests to `http://localhost:8000`.

## Environment Variables

Reference values from [.env.example](.env.example):

```env
DATABASE_URL=sqlite:///./data/skillhub.db
SECRET_KEY=dev-secret-key-change-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ALGORITHM=HS256
FRONTEND_URL=http://localhost:5173
STORAGE_ROOT=./storage
VITE_API_BASE_URL=/api
```

## MCP Usage

### Single skill

```text
/mcp/{workspace_id}/{skill_id}
```

### Workspace aggregator

```text
/mcp/workspaces/{workspace_id}
```
