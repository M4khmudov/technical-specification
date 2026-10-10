from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, UnauthorizedError
from app.core.permissions import ROLE_PERMISSIONS, Permission
from app.core.security import create_access_token, verify_password
from app.models import User
from app.models.enums import ROLE_LABELS, Role

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    login: str
    password: str


class UserOut(BaseModel):
    id: int
    login: str
    full_name: str
    role: Role
    role_label: str
    permissions: list[Permission]  # интерфейс по ним прячет недоступные кнопки


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 — тип токена, не пароль
    user: UserOut


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        login=user.login,
        full_name=user.full_name,
        role=user.role,
        role_label=ROLE_LABELS[user.role],
        permissions=sorted(ROLE_PERMISSIONS[user.role]),
    )


@router.post("/login")
async def login(data: LoginIn, session: SessionDep) -> TokenOut:
    user = await session.scalar(select(User).where(User.login == data.login))
    if user is None or not user.is_active or not verify_password(data.password, user.password_hash):
        raise UnauthorizedError("Неверный логин или пароль")
    return TokenOut(access_token=create_access_token(user.id), user=user_out(user))


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return user_out(user)
