import hashlib
import secrets
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select

from app.dependencies import CurrentUserDep
from app.dependencies import SessionDep
from app.models import ApiKey, TeamMembership, Workspace

router = APIRouter()


class UserResponse(BaseModel):
    id: int
    account: str


class ApiKeyCreate(BaseModel):
    workspace_id: int


class ApiKeyResponse(BaseModel):
    id: int
    workspace_id: int
    workspace_name: str
    created_at: str
    token: str | None = None


@router.get("/me", response_model=UserResponse)
async def read_current_user(user: CurrentUserDep):
    return user


def _require_workspace_access(session, user_id: int, workspace_id: int) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if workspace.type == "personal":
        if workspace.owner_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return workspace
    membership = session.exec(
        select(TeamMembership).where(
            TeamMembership.team_id == workspace.team_id,
            TeamMembership.user_id == user_id,
        )
    ).first()
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return workspace


@router.get("/me/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(session: SessionDep, user: CurrentUserDep):
    keys = session.exec(select(ApiKey).where(ApiKey.user_id == user.id)).all()
    return [
        ApiKeyResponse(
            id=key.id,
            workspace_id=key.workspace_id,
            workspace_name=key.workspace.name if key.workspace else f"Workspace {key.workspace_id}",
            created_at=key.created_at.isoformat(),
        )
        for key in keys
    ]


@router.post("/me/api-keys", response_model=ApiKeyResponse)
async def create_api_key(data: ApiKeyCreate, session: SessionDep, user: CurrentUserDep):
    workspace = _require_workspace_access(session, user.id, data.workspace_id)
    token = f"skh_{secrets.token_urlsafe(24)}"
    key = session.exec(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.workspace_id == workspace.id)
    ).first()
    if key:
        key.key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        key.created_at = datetime.utcnow()
    else:
        key = ApiKey(
            user_id=user.id,
            workspace_id=workspace.id,
            key_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
    session.add(key)
    session.commit()
    session.refresh(key)
    return ApiKeyResponse(
        id=key.id,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        created_at=key.created_at.isoformat(),
        token=token,
    )


@router.delete("/me/api-keys/{key_id}")
async def delete_api_key(key_id: int, session: SessionDep, user: CurrentUserDep):
    key = session.get(ApiKey, key_id)
    if not key or key.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    session.delete(key)
    session.commit()
    return {"ok": True}
