from sqlmodel import select
from app.database import get_session
from app.models import User, Workspace, TeamMembership, Skill


async def can_access_workspace(user: User, workspace_id: int) -> bool:
    for session in get_session():
        workspace = session.get(Workspace, workspace_id)
        if not workspace:
            return False
        if workspace.type == "personal":
            return workspace.owner_id == user.id
        # team workspace
        membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == workspace.team_id,
                TeamMembership.user_id == user.id,
            )
        ).first()
        return membership is not None
    return False


async def can_manage_workspace(user: User, workspace_id: int) -> bool:
    for session in get_session():
        workspace = session.get(Workspace, workspace_id)
        if not workspace:
            return False
        if workspace.type == "personal":
            return workspace.owner_id == user.id
        membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == workspace.team_id,
                TeamMembership.user_id == user.id,
            )
        ).first()
        return membership is not None and membership.role == "admin"
    return False


async def can_access_skill(user: User | None, skill: Skill) -> bool:
    if user is None:
        return False
    return await can_access_workspace(user, skill.workspace_id)


def is_skill_visible_in_workspace(workspace: Workspace, skill: Skill, membership: TeamMembership | None = None) -> bool:
    if workspace.type == "team":
        if not skill.enabled:
            return False
        if membership is None:
            return False
        selected = membership.skill_preferences or {}
        if not membership.skill_preferences_configured:
            return True
        return selected.get(str(skill.id), False)
    return True
