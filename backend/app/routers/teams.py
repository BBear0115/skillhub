from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select
from app.core.permissions import current_runtime_version, is_public_runtime_skill, workspace_skill_exposure_enabled
from app.dependencies import SessionDep, CurrentUserDep
from app.models import Skill, Team, TeamJoinRequest, TeamMembership, User, Workspace

router = APIRouter()


class TeamCreate(BaseModel):
    name: str


class TeamResponse(BaseModel):
    id: int
    name: str
    owner_id: int
    membership_role: str | None = None
    has_pending_request: bool = False


class TeamMemberCreate(BaseModel):
    account: str
    role: str = "member"


class TeamMemberResponse(BaseModel):
    user_id: int
    account: str
    role: str


class TeamJoinRequestCreate(BaseModel):
    team_id: int


class TeamJoinRequestResponse(BaseModel):
    id: int
    team_id: int
    team_name: str
    user_id: int
    account: str
    status: str


class TeamJoinRequestDecision(BaseModel):
    approve: bool


class TeamSkillPreferenceUpdate(BaseModel):
    enabled_skill_ids: list[int]


class TeamSelectableSkillResponse(BaseModel):
    id: int
    name: str
    description: str | None
    workspace_id: int
    visibility: str
    source: str


def _require_team_admin(team_id: int, user_id: int, session) -> Team:
    team = session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    membership = session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).first()
    if not membership or membership.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return team


def _require_team_member(team_id: int, user_id: int, session) -> Team:
    team = session.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    membership = session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).first()
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Team access required")
    return team


def _get_membership(team_id: int, user_id: int, session) -> TeamMembership | None:
    return session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user_id)
    ).first()


def _team_workspace(session, team_id: int) -> Workspace:
    workspace = session.exec(select(Workspace).where(Workspace.team_id == team_id)).first()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


def _selectable_skills(session, team: Team) -> list[Skill]:
    workspace = _team_workspace(session, team.id)
    local_skills = session.exec(select(Skill).where(Skill.workspace_id == workspace.id)).all()
    public_skills = session.exec(select(Skill).where(Skill.visibility == "public")).all()
    skills: dict[int, Skill] = {}
    for skill in local_skills:
        if current_runtime_version(session, skill) is not None and (
            skill.visibility == "public" or workspace_skill_exposure_enabled(session, workspace, skill.id)
        ):
            skills[skill.id] = skill
    for skill in public_skills:
        if is_public_runtime_skill(session, skill):
            skills[skill.id] = skill
    return sorted(skills.values(), key=lambda item: (item.name.lower(), item.id))


@router.post("", response_model=TeamResponse)
async def create_team(data: TeamCreate, session: SessionDep, user: CurrentUserDep):
    team = Team(name=data.name, owner_id=user.id)
    session.add(team)
    session.commit()
    session.refresh(team)

    membership = TeamMembership(team_id=team.id, user_id=user.id, role="admin")
    session.add(membership)

    workspace = Workspace(name=data.name, type="team", owner_id=user.id, team_id=team.id)
    session.add(workspace)
    session.commit()

    return team


@router.get("", response_model=list[TeamResponse])
async def list_teams(session: SessionDep, user: CurrentUserDep):
    memberships = session.exec(select(TeamMembership).where(TeamMembership.user_id == user.id)).all()
    pending = session.exec(
        select(TeamJoinRequest).where(TeamJoinRequest.user_id == user.id, TeamJoinRequest.status == "pending")
    ).all()
    pending_team_ids = {item.team_id for item in pending}
    team_ids = [membership.team_id for membership in memberships]
    teams = session.exec(select(Team).where(Team.id.in_(team_ids))).all() if team_ids else []
    membership_map = {item.team_id: item for item in memberships}
    return [
        TeamResponse(
            id=team.id,
            name=team.name,
            owner_id=team.owner_id,
            membership_role=membership_map[team.id].role if team.id in membership_map else None,
            has_pending_request=team.id in pending_team_ids,
        )
        for team in teams
    ]


@router.get("/{team_id}/members", response_model=list[TeamMemberResponse])
async def list_team_members(team_id: int, session: SessionDep, user: CurrentUserDep):
    _require_team_member(team_id, user.id, session)
    memberships = session.exec(select(TeamMembership).where(TeamMembership.team_id == team_id)).all()
    user_ids = [membership.user_id for membership in memberships]
    users = session.exec(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
    user_map = {member.id: member for member in users}
    return [
        TeamMemberResponse(
            user_id=membership.user_id,
            account=user_map[membership.user_id].account,
            role=membership.role,
        )
        for membership in memberships
        if membership.user_id in user_map
    ]


@router.post("/{team_id}/members", response_model=TeamMemberResponse)
async def add_team_member(team_id: int, data: TeamMemberCreate, session: SessionDep, user: CurrentUserDep):
    _require_team_admin(team_id, user.id, session)
    if data.role not in {"admin", "member"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")

    target_user = session.exec(select(User).where(User.account == data.account)).first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    membership = session.exec(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == target_user.id)
    ).first()
    if membership:
        membership.role = data.role
    else:
        membership = TeamMembership(team_id=team_id, user_id=target_user.id, role=data.role)
    session.add(membership)
    session.commit()

    return TeamMemberResponse(
        user_id=target_user.id,
        account=target_user.account,
        role=membership.role,
    )


@router.get("/discover", response_model=list[TeamResponse])
async def discover_teams(session: SessionDep, user: CurrentUserDep):
    memberships = session.exec(select(TeamMembership).where(TeamMembership.user_id == user.id)).all()
    membership_map = {item.team_id: item for item in memberships}
    pending = session.exec(
        select(TeamJoinRequest).where(TeamJoinRequest.user_id == user.id, TeamJoinRequest.status == "pending")
    ).all()
    pending_team_ids = {item.team_id for item in pending}
    teams = session.exec(select(Team)).all()
    return [
        TeamResponse(
            id=team.id,
            name=team.name,
            owner_id=team.owner_id,
            membership_role=membership_map[team.id].role if team.id in membership_map else None,
            has_pending_request=team.id in pending_team_ids,
        )
        for team in teams
        if team.id not in membership_map
    ]


@router.post("/join-requests", response_model=TeamJoinRequestResponse)
async def create_join_request(data: TeamJoinRequestCreate, session: SessionDep, user: CurrentUserDep):
    team = session.get(Team, data.team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    membership = _get_membership(data.team_id, user.id, session)
    if membership:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already a team member")
    pending = session.exec(
        select(TeamJoinRequest).where(
            TeamJoinRequest.team_id == data.team_id,
            TeamJoinRequest.user_id == user.id,
            TeamJoinRequest.status == "pending",
        )
    ).first()
    if pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Join request already pending")
    request = TeamJoinRequest(team_id=data.team_id, user_id=user.id)
    session.add(request)
    session.commit()
    session.refresh(request)
    return TeamJoinRequestResponse(
        id=request.id,
        team_id=team.id,
        team_name=team.name,
        user_id=user.id,
        account=user.account,
        status=request.status,
    )


@router.get("/{team_id}/join-requests", response_model=list[TeamJoinRequestResponse])
async def list_join_requests(team_id: int, session: SessionDep, user: CurrentUserDep):
    team = _require_team_admin(team_id, user.id, session)
    requests = session.exec(
        select(TeamJoinRequest).where(TeamJoinRequest.team_id == team_id, TeamJoinRequest.status == "pending")
    ).all()
    user_ids = [item.user_id for item in requests]
    users = session.exec(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
    user_map = {item.id: item for item in users}
    return [
        TeamJoinRequestResponse(
            id=item.id,
            team_id=team.id,
            team_name=team.name,
            user_id=item.user_id,
            account=user_map[item.user_id].account if item.user_id in user_map else f"user-{item.user_id}",
            status=item.status,
        )
        for item in requests
    ]


@router.post("/{team_id}/join-requests/{request_id}", response_model=TeamJoinRequestResponse)
async def decide_join_request(team_id: int, request_id: int, data: TeamJoinRequestDecision, session: SessionDep, user: CurrentUserDep):
    team = _require_team_admin(team_id, user.id, session)
    request = session.get(TeamJoinRequest, request_id)
    if not request or request.team_id != team_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Join request not found")
    target_user = session.get(User, request.user_id)
    if request.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Join request already handled")
    if data.approve:
        membership = _get_membership(team_id, request.user_id, session)
        if not membership:
            membership = TeamMembership(team_id=team_id, user_id=request.user_id, role="member")
            session.add(membership)
        request.status = "approved"
    else:
        request.status = "rejected"
    session.add(request)
    session.commit()
    return TeamJoinRequestResponse(
        id=request.id,
        team_id=team.id,
        team_name=team.name,
        user_id=request.user_id,
        account=target_user.account if target_user else f"user-{request.user_id}",
        status=request.status,
    )


@router.get("/{team_id}/me/skills")
async def read_my_skill_preferences(team_id: int, session: SessionDep, user: CurrentUserDep):
    team = _require_team_member(team_id, user.id, session)
    membership = _get_membership(team_id, user.id, session)
    selectable = _selectable_skills(session, team)
    allowed_ids = {skill.id for skill in selectable}
    enabled_ids = [int(key) for key, value in (membership.skill_preferences or {}).items() if value and int(key) in allowed_ids] if membership else []
    if membership and not membership.skill_preferences_configured:
        enabled_ids = sorted(allowed_ids)
    return {
        "enabled_skill_ids": enabled_ids,
        "configured": membership.skill_preferences_configured if membership else False,
    }


@router.get("/{team_id}/selectable-skills", response_model=list[TeamSelectableSkillResponse])
async def list_selectable_skills(team_id: int, session: SessionDep, user: CurrentUserDep):
    team = _require_team_member(team_id, user.id, session)
    workspace = _team_workspace(session, team.id)
    results: list[TeamSelectableSkillResponse] = []
    for skill in _selectable_skills(session, team):
        results.append(
            TeamSelectableSkillResponse(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                workspace_id=skill.workspace_id,
                visibility=skill.visibility,
                source="public" if skill.visibility == "public" else ("team" if skill.workspace_id == workspace.id else "external"),
            )
        )
    return results


@router.put("/{team_id}/me/skills")
async def update_my_skill_preferences(team_id: int, data: TeamSkillPreferenceUpdate, session: SessionDep, user: CurrentUserDep):
    team = _require_team_member(team_id, user.id, session)
    membership = _get_membership(team_id, user.id, session)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    selectable = _selectable_skills(session, team)
    valid_skill_ids = {skill.id for skill in selectable}
    invalid = sorted(set(data.enabled_skill_ids) - valid_skill_ids)
    if invalid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown enabled skills: {invalid}")
    membership.skill_preferences = {str(skill_id): True for skill_id in data.enabled_skill_ids}
    membership.skill_preferences_configured = True
    session.add(membership)
    session.commit()
    return {"enabled_skill_ids": data.enabled_skill_ids, "configured": True}
