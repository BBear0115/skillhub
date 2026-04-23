from fastapi import APIRouter
from pydantic import BaseModel

from app.core.permissions import is_super_admin_user
from app.dependencies import CurrentUserDep

router = APIRouter()


class UserResponse(BaseModel):
    id: int
    account: str
    is_super_admin: bool

@router.get("/me", response_model=UserResponse)
async def read_current_user(user: CurrentUserDep):
    return UserResponse(id=user.id, account=user.account, is_super_admin=is_super_admin_user(user))
