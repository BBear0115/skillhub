from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select

from app.core.permissions import can_access_workspace, current_runtime_version
from app.dependencies import CurrentUserDep, SessionDep
from app.models import Skill, SkillVersion, Tool

router = APIRouter()


class ToolResponse(BaseModel):
    id: int
    skill_id: int
    skill_version_id: int | None
    name: str
    description: str | None
    input_schema: dict


@router.post("/skills/{skill_id}/tools")
async def create_tool_removed(skill_id: int):
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Direct tool editing has been removed. Upload a ZIP version instead.",
    )


@router.put("/tools/{tool_id}")
async def update_tool_removed(tool_id: int):
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Direct tool editing has been removed. Upload a ZIP version instead.",
    )


@router.delete("/tools/{tool_id}")
async def delete_tool_removed(tool_id: int):
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Direct tool editing has been removed. Upload a ZIP version instead.",
    )


@router.get("/skills/{skill_id}/tools", response_model=list[ToolResponse])
async def list_tools(skill_id: int, session: SessionDep, user: CurrentUserDep):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not await can_access_workspace(user, skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    version = current_runtime_version(session, skill)
    if version is None:
        return []
    tools = session.exec(select(Tool).where(Tool.skill_version_id == version.id)).all()
    return tools
