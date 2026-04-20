from fastapi import APIRouter, HTTPException, status
from sqlmodel import select
from pydantic import BaseModel

from app.dependencies import SessionDep
from app.models import User
from app.core.security import verify_password, get_password_hash, create_access_token

router = APIRouter()


class RegisterRequest(BaseModel):
    account: str
    password: str


class LoginRequest(BaseModel):
    account: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=TokenResponse)
async def register(data: RegisterRequest, session: SessionDep):
    existing = session.exec(select(User).where(User.account == data.account)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account already registered")
    user = User(account=data.account, hashed_password=get_password_hash(data.password))
    session.add(user)
    session.commit()
    session.refresh(user)

    # Auto-create personal workspace
    from app.models import Workspace
    workspace = Workspace(name=f"{user.account}'s Personal", type="personal", owner_id=user.id)
    session.add(workspace)
    session.commit()

    access_token = create_access_token(data={"sub": str(user.id)})
    return TokenResponse(access_token=access_token)


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, session: SessionDep):
    user = session.exec(select(User).where(User.account == data.account)).first()
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect account or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": str(user.id)})
    return TokenResponse(access_token=access_token)
