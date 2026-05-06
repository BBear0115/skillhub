# Repository Guidelines

## Project Structure & Module Organization

SkillHub is split into a FastAPI backend and a Vite/React frontend.

- `backend/app/`: API code: routers, models, services, database setup, config, and security.
- `backend/alembic/`: migration environment and versioned migrations.
- `backend/tests/`: pytest suite for backend behavior and MCP/runtime flows.
- `frontend/src/`: React and TypeScript application source.
- `frontend/public/`: static frontend assets.
- `examples/`: sample Skill packages such as `echo-skill` and `server-transfer-skill`.
- `scripts/`: local helpers, especially `run-skillhub.ps1`.

Runtime artifacts such as `.env`, logs, storage, SQLite databases, uploaded ZIPs, and build output should stay untracked.

## Build, Test, and Development Commands

- `powershell -ExecutionPolicy Bypass -File .\scripts\run-skillhub.ps1`: run both services locally.
- `cd backend; python -m pip install -e .`: install the backend package.
- `cd backend; python -m pip install -e ".[dev]"`: install backend development dependencies.
- `cd backend; pytest`: run backend tests.
- `cd backend; ruff check .`: lint backend Python code.
- `cd frontend; npm ci`: install locked frontend dependencies.
- `cd frontend; npm run dev`: start the Vite development server.
- `cd frontend; npm run build`: type-check and build the frontend.
- `cd frontend; npm test`: run Vitest.

## Coding Style & Naming Conventions

Backend code targets Python 3.12 and uses Ruff with a 100-character line length. Use snake_case modules, PascalCase classes, and descriptive route/service functions. Prefer typed Pydantic/SQLModel models at API and persistence boundaries.

Frontend code uses TypeScript, React 18, and Tailwind. Keep components PascalCase, hooks camelCase beginning with `use`, and utility modules camelCase or domain-specific names under `frontend/src/lib/`.

## Testing Guidelines

Backend tests use pytest with `pytest-asyncio` enabled automatically. Name files `test_*.py` under `backend/tests/`. Add focused tests for API changes, permissions, Skill package parsing, MCP behavior, and runtime deployment.

Frontend tests use Vitest. Co-locate new tests near covered code or follow the existing `src` layout.

## Commit & Pull Request Guidelines

Recent history uses concise Conventional Commit-style messages, for example `feat: add super admin runtime workbench`, `fix(mcp): load tool definitions within active session`, and `chore: sanitize github release and update docs`.

Pull requests should stay scoped, describe behavior changes, mention related issues, and include screenshots for UI changes. Update docs or `.env.example` when setup, configuration, or user-facing behavior changes.

## Security & Configuration Tips

Copy `.env.example` to `backend/.env` for local work and replace placeholders. Never commit real secrets, access tokens, databases, storage directories, logs, uploaded packages, or generated runtime artifacts.
