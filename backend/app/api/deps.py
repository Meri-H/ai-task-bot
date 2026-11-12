import secrets
from collections.abc import AsyncIterator

from arq.connections import ArqRedis
from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings


def token_is_valid(settings: Settings, supplied: str | None) -> bool:
    """True when auth is disabled, or the supplied token matches (constant-time compare)."""
    if not settings.dashboard_token:
        return True
    if not supplied:
        return False
    return secrets.compare_digest(supplied.encode(), settings.dashboard_token.encode())


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_redis(request: Request) -> ArqRedis:
    return request.app.state.redis


async def require_auth(request: Request) -> None:
    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    if not token_is_valid(request.app.state.settings, supplied if scheme.lower() == "bearer" else None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )
