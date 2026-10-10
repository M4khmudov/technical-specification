from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.core.security import decode_access_token
from app.db.session import get_session
from app.models import User
from app.services.rates import RatesProvider, get_rates_provider

_bearer = HTTPBearer(auto_error=False)


class UnauthorizedError(DomainError):
    status_code = 401


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    user_id = decode_access_token(credentials.credentials) if credentials else None
    user = await session.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise UnauthorizedError("Требуется вход в систему")
    return user


SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
Rates = Annotated[RatesProvider, Depends(get_rates_provider)]
