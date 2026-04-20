# SkillHub

Open-source skill management and MCP gateway for individuals and teams.

SkillHub helps teams organize skills, control who can manage them, and expose them to agents through MCP-compatible endpoints. It supports personal workspaces, team workspaces, ZIP-based skill import, inline skills, workspace-level discovery, and API-key access.

## Why SkillHub

Teams often end up with useful prompts, tools, scripts, and skill packs spread across chats, local folders, and ad hoc agent setups. SkillHub gives those skills a shared home:

- manage skills in personal and team workspaces
- control which team members can manage skills
- let members choose which shared skills they want to use
- expose skills through single-skill or workspace-level MCP endpoints
- issue workspace-scoped API keys for agent integration

## Core Features

### Workspace model

- Personal workspace created automatically on registration
- Team workspace created automatically when a team is created
- Join-request flow for team membership
- Workspace-scoped API keys

### Skill management

- Create inline skills directly from the UI
- Upload ZIP skill packages
- Import repository-style skill archives with `marketplace.json`
- Copy skills from personal space into team space
- Delete and maintain skills from the console

### Team controls

- Team admins manage the team's skill catalog
- Team members can discover and use only the skills that are exposed to them
- Members can keep a smaller personal selection from the available team skills

### MCP support

- Single skill endpoint: `/mcp/{workspace_id}/{skill_id}`
- Workspace aggregator endpoint: `/mcp/workspaces/{workspace_id}`
- Aggregator tools:
  - `skills_list`
  - `skill_tools`
  - `skill_call`

## Architecture

### Backend

- Python 3.12+
- FastAPI
- SQLModel
- Alembic
- Uvicorn

### Frontend

- React 18
- TypeScript
- Vite
- Tailwind CSS

## Repository Layout

```text
skillhub/
|-- backend/
|   |-- app/
|   |   |-- core/
|   |   |-- models/
|   |   |-- routers/
|   |   `-- services/
|   |-- alembic/
|   |-- Dockerfile
|   `-- pyproject.toml
|-- frontend/
|   |-- public/
|   |-- src/
|   |-- Dockerfile
|   `-- package.json
|-- examples/
|-- scripts/
|-- .github/workflows/
|-- CONTRIBUTING.md
|-- SECURITY.md
|-- LICENSE
`-- README.md
```

## Quick Start

### Prerequisites

- Windows PowerShell for the provided local script
- Python available for the backend
- Node.js and npm for the frontend

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

### Run locally

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-skillhub.ps1
```

Default local URLs:

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8000`
- OpenAPI docs: `http://127.0.0.1:8000/docs`

## Docker Compose

The project includes:

- [docker-compose.yml](docker-compose.yml)
- [backend/Dockerfile](backend/Dockerfile)
- [frontend/Dockerfile](frontend/Dockerfile)

Run:

```bash
docker compose up --build
```

In container mode:

- PostgreSQL is used as the database
- Backend is exposed on port `8000`
- Frontend is exposed on port `5173`

## Environment Variables

### Backend

Reference values from [.env.example](.env.example):

```env
DATABASE_URL=sqlite:///./skillhub.db
SECRET_KEY=dev-secret-key-change-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ALGORITHM=HS256
FRONTEND_URL=http://localhost:5173
STORAGE_ROOT=./storage
```

### Frontend

```env
VITE_API_BASE_URL=http://localhost:8000
```

## MCP Usage

### Single skill

Use when the caller already knows which skill should be invoked:

```text
/mcp/{workspace_id}/{skill_id}
```

Typical flow:

1. `initialize`
2. `tools/list`
3. `tools/call`

### Workspace aggregator

Use when the caller needs to discover available skills first:

```text
/mcp/workspaces/{workspace_id}
```

Typical flow:

1. `initialize`
2. `tools/list`
3. `tools/call -> skills_list`
4. `tools/call -> skill_tools`
5. `tools/call -> skill_call`

## Authentication

SkillHub supports:

- Bearer tokens from user login
- Workspace API keys

Use either in the `Authorization` header:

```http
Authorization: Bearer <token-or-api-key>
```

## Example Skill

A minimal example skill is included in [examples/echo-skill](examples/echo-skill/README.md).

After uploading it:

- one tool named `echo` is registered
- calling it with `{"text":"hello"}` returns `echo:hello`

## API Overview

### Authentication

Register:

```http
POST /auth/register
Content-Type: application/json
```

```json
{
  "account": "demo",
  "password": "demo"
}
```

Login:

```http
POST /auth/login
Content-Type: application/json
```

```json
{
  "account": "demo",
  "password": "demo"
}
```

### Workspaces

```http
GET /workspaces
Authorization: Bearer <token>
```

### Teams

Create team:

```http
POST /teams
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "name": "Growth Agents"
}
```

Add member:

```http
POST /teams/{team_id}/members
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "account": "member-account",
  "role": "member"
}
```

### Skills

Create inline skill:

```http
POST /workspaces/{workspace_id}/skills
Authorization: Bearer <token>
Content-Type: application/json
```

Upload ZIP skill:

```http
POST /workspaces/{workspace_id}/skills/upload
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

### API keys

```http
POST /users/me/api-keys
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "workspace_id": 1
}
```

## Open-Source Notes

- `LICENSE`: MIT
- contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- security reporting guidance: [SECURITY.md](SECURITY.md)
- CI workflow: [ci.yml](.github/workflows/ci.yml)

## Roadmap Ideas

- richer skill metadata and search
- better skill package validation and sandboxing
- audit logs for team administration
- stronger production controls around executable skill packages
