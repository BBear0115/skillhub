# Contributing

Thanks for contributing to SkillHub.

## Development Setup

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

## Local Run

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-skillhub.ps1
```

## Pull Request Guidelines

- Keep changes scoped and reviewable.
- Update `README.md` when user-facing behavior changes.
- Prefer backward-compatible API changes.
- Avoid checking in runtime data, databases, or build output.

## Project Focus

SkillHub is intended to help teams:

- organize personal and team skills
- safely expose skills over MCP
- let members discover and use only the skills relevant to them
