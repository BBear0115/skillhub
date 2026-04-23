from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import select
from app.dependencies import SessionDep, CurrentUserDep
from app.core.permissions import is_super_admin_user
from app.models import Workspace, TeamMembership

router = APIRouter()


class WorkspaceResponse(BaseModel):
    id: int
    name: str
    type: str
    owner_id: int
    team_id: int | None


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(session: SessionDep, user: CurrentUserDep):
    personal = session.exec(select(Workspace).where(Workspace.owner_id == user.id, Workspace.type == "personal")).all()
    team_memberships = session.exec(select(TeamMembership).where(TeamMembership.user_id == user.id)).all()
    team_ids = [m.team_id for m in team_memberships]
    team_workspaces = []
    if team_ids:
        team_workspaces = session.exec(select(Workspace).where(Workspace.team_id.in_(team_ids))).all()
    admin_workspaces = []
    if is_super_admin_user(user):
        admin_workspaces = session.exec(select(Workspace).where(Workspace.owner_id == user.id, Workspace.type == "admin")).all()
    return list(admin_workspaces) + list(personal) + list(team_workspaces)
