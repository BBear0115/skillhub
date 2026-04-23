from sqlmodel import select

from app.config import settings
from app.database import get_session
from app.models import Skill, SkillVersion, TeamMembership, User, Workspace, WorkspaceSkillExposure


def is_super_admin_user(user: User | None) -> bool:
    if user is None:
        return False
    if settings.super_admin_account:
        return user.account == settings.super_admin_account
    return user.id == 1


async def can_access_workspace(user: User, workspace_id: int) -> bool:
    for session in get_session():
        workspace = session.get(Workspace, workspace_id)
        if not workspace:
            return False
        if workspace.type == "admin":
            return workspace.owner_id == user.id and is_super_admin_user(user)
        if workspace.type == "personal":
            return workspace.owner_id == user.id
        membership = session.exec(
            select(TeamMembership).where(
                TeamMembership.team_id == workspace.team_id,
                TeamMembership.user_id == user.id,
            )
        ).first()
        return membership is not None
    return False


async def can_install_to_workspace(user: User, workspace_id: int) -> bool:
    return await can_access_workspace(user, workspace_id)


async def can_manage_workspace(user: User, workspace_id: int) -> bool:
    for session in get_session():
        workspace = session.get(Workspace, workspace_id)
        if not workspace:
            return False
        if workspace.type == "admin":
            return workspace.owner_id == user.id and is_super_admin_user(user)
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


async def can_review_skill(user: User) -> bool:
    return is_super_admin_user(user)


async def can_access_skill(user: User | None, skill: Skill) -> bool:
    if user is None:
        return False
    return await can_access_workspace(user, skill.workspace_id)


def workspace_skill_exposure_enabled(session, workspace: Workspace, skill_id: int) -> bool:
    if workspace.type != "team":
        return True
    exposure = session.get(WorkspaceSkillExposure, (workspace.id, skill_id))
    return bool(exposure and exposure.enabled)


def team_member_skill_enabled(membership: TeamMembership | None, skill_id: int) -> bool:
    if membership is None or not membership.skill_preferences_configured:
        return True
    return bool((membership.skill_preferences or {}).get(str(skill_id), False))


def current_runtime_version(session, skill: Skill) -> SkillVersion | None:
    if skill.current_approved_version_id is None or skill.deployed_version_id is None:
        return None
    if skill.current_approved_version_id != skill.deployed_version_id:
        return None
    version = session.get(SkillVersion, skill.current_approved_version_id)
    if not version or version.status != "approved" or version.deploy_status != "deployed" or not version.published_mcp_endpoint_url:
        return None
    return version


def is_public_runtime_skill(session, skill: Skill) -> bool:
    return skill.visibility == "public" and current_runtime_version(session, skill) is not None


def is_skill_visible_in_workspace(session, workspace: Workspace, skill: Skill) -> bool:
    if current_runtime_version(session, skill) is None:
        return False
    if skill.visibility == "public":
        return True
    if workspace.type == "team":
        return workspace_skill_exposure_enabled(session, workspace, skill.id)
    return True
