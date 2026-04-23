from contextlib import asynccontextmanager
import logging
import re
from urllib.parse import urlparse
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

import app.database as database_module
from app.config import settings
from app.database import init_db
from app.core.security import get_password_hash, verify_password
from app.models import User, Workspace
from app.routers import auth, users, teams, workspaces, skills, tools, mcp
from app.services.skill_packages import ensure_storage_root

logger = logging.getLogger(__name__)


def _ensure_super_admin() -> None:
    if not settings.super_admin_account:
        return
    with Session(database_module.engine) as session:
        user = session.exec(select(User).where(User.account == settings.super_admin_account)).first()
        if user is None:
            user = User(
                account=settings.super_admin_account,
                hashed_password=get_password_hash(settings.super_admin_password),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        else:
            if not verify_password(settings.super_admin_password, user.hashed_password):
                user.hashed_password = get_password_hash(settings.super_admin_password)
                session.add(user)
                session.commit()
                session.refresh(user)

        workspace = session.exec(
            select(Workspace).where(Workspace.owner_id == user.id, Workspace.type == "personal")
        ).first()
        if workspace is None:
            session.add(Workspace(name=f"{user.account}'s Personal", type="personal", owner_id=user.id))
            session.commit()

        admin_workspace = session.exec(
            select(Workspace).where(Workspace.owner_id == user.id, Workspace.type == "admin")
        ).first()
        if admin_workspace is None:
            session.add(Workspace(name="Super Admin Workbench", type="admin", owner_id=user.id))
            session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _ensure_super_admin()
    storage_root = ensure_storage_root()
    logger.info(
        "SkillHub startup config: database_url=%s storage_root=%s frontend_url=%s super_admin_account=%s",
        settings.database_url,
        storage_root,
        settings.frontend_url,
        settings.super_admin_account,
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


def _build_allowed_origin_regex() -> str:
    # Allow common private-network/dev origins so the frontend can be opened
    # directly from a LAN IP without having to rewrite env config first.
    return r"^https?://(localhost|127\.0\.0\.1|10(?:\.\d{1,3}){3}|172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}|192\.168(?:\.\d{1,3}){2})(:\d+)?$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_allowed_origins(),
    allow_origin_regex=_build_allowed_origin_regex(),
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
