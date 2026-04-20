from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select
from app.dependencies import SessionDep, CurrentUserDep
from app.models import Tool, Skill
from app.core.permissions import can_access_workspace, can_manage_workspace

router = APIRouter()


class ToolCreate(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict | None = None


class ToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    input_schema: dict | None = None


class ToolResponse(BaseModel):
    id: int
    skill_id: int
    name: str
    description: str | None
    input_schema: dict


@router.post("/skills/{skill_id}/tools", response_model=ToolResponse)
async def create_tool(skill_id: int, data: ToolCreate, session: SessionDep, user: CurrentUserDep):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not await can_manage_workspace(user, skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    tool = Tool(
        skill_id=skill_id,
        name=data.name,
        description=data.description,
        input_schema=data.input_schema or {},
    )
    session.add(tool)
    session.commit()
    session.refresh(tool)
    return tool


@router.get("/skills/{skill_id}/tools", response_model=list[ToolResponse])
async def list_tools(skill_id: int, session: SessionDep, user: CurrentUserDep):
    skill = session.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.visibility != "public" and not await can_access_workspace(user, skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    tools = session.exec(select(Tool).where(Tool.skill_id == skill_id)).all()
    return tools


@router.put("/tools/{tool_id}", response_model=ToolResponse)
async def update_tool(tool_id: int, data: ToolUpdate, session: SessionDep, user: CurrentUserDep):
    tool = session.get(Tool, tool_id)
    if not tool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    skill = session.get(Skill, tool.skill_id)
    if not skill or not await can_manage_workspace(user, skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tool, key, value)
    session.add(tool)
    session.commit()
    session.refresh(tool)
    return tool


@router.delete("/tools/{tool_id}")
async def delete_tool(tool_id: int, session: SessionDep, user: CurrentUserDep):
    tool = session.get(Tool, tool_id)
    if not tool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    skill = session.get(Skill, tool.skill_id)
    if not skill or not await can_manage_workspace(user, skill.workspace_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    session.delete(tool)
    session.commit()
    return {"ok": True}
