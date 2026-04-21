from contextlib import asynccontextmanager
import logging
from urllib.parse import urlparse
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routers import auth, users, teams, workspaces, skills, tools, mcp
from app.services.skill_packages import ensure_storage_root

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    storage_root = ensure_storage_root()
    logger.info(
        "SkillHub startup config: database_url=%s storage_root=%s frontend_url=%s",
        settings.database_url,
        storage_root,
        settings.frontend_url,
    )
    yield


app = FastAPI(
    title="SkillHub",
    description="Open Skill management and MCP gateway for individuals and teams.",
    version="0.1.0",
    lifespan=lifespan,
)


def _build_allowed_origins() -> list[str]:
    origins = {settings.frontend_url, "http://localhost:5173", "http://127.0.0.1:5173"}
    parsed = urlparse(settings.frontend_url)
    if parsed.scheme and parsed.port:
        origins.add(f"{parsed.scheme}://localhost:{parsed.port}")
        origins.add(f"{parsed.scheme}://127.0.0.1:{parsed.port}")
    return sorted(origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(teams.router, prefix="/teams", tags=["teams"])
app.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
app.include_router(skills.router, tags=["skills"])
app.include_router(tools.router, tags=["tools"])
app.include_router(mcp.router, prefix="/mcp", tags=["mcp"])


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "skillhub", "version": app.version}


@app.get("/")
async def root():
    return {
        "name": app.title,
        "version": app.version,
        "docs": "/docs",
        "health": "/health",
        "message": "SkillHub is running",
    }
