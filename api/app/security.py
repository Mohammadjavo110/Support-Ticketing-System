"""Password hashing, session cookie handling and auth dependencies."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request, Response
from passlib.context import CryptContext
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import AuthSession, User


def _patch_bcrypt_for_passlib() -> None:
    """Compatibility shim: passlib 1.7.4 vs bcrypt >= 5.0.

    passlib probes the backend with a 255-byte secret and expects the silent
    72-byte truncation older bcrypt performed; bcrypt 5 raises instead, which
    makes every passlib hash/verify fail. requirements.txt is frozen, so restore
    the pre-5.0 contract here. Also expose __about__ so passlib can read the
    backend version without a trapped warning.
    """
    if not hasattr(bcrypt, "__about__"):
        about = type("__about__", (), {"__version__": getattr(bcrypt, "__version__", "unknown")})
        bcrypt.__about__ = about
    original_hashpw = bcrypt.hashpw

    def hashpw(password, salt):
        if isinstance(password, (bytes, bytearray)) and len(password) > 72:
            password = password[:72]
        return original_hashpw(password, salt)

    bcrypt.hashpw = hashpw


_patch_bcrypt_for_passlib()

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def hash_token(token: str) -> str:
    # Only the sha256 digest of the random token is stored, never the token itself.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _expired(expires_at: datetime) -> bool:
    # Postgres returns aware timestamps; be tolerant of naive values.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


async def create_session(db: AsyncSession, user: User, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_TTL_HOURS)
    db.add(AuthSession(user_id=user.id, token_hash=hash_token(token), expires_at=expires_at))
    await db.commit()
    response.set_cookie(
        key="session",
        value=token,
        max_age=settings.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,  # local dev over http; flip behind TLS
    )


async def current_user(
    request: Request, db: AsyncSession = Depends(get_session)
) -> User:
    """Resolve the logged-in user from the session cookie, or raise 401."""
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    row = await db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token))
    )
    if row is None or _expired(row.expires_at):
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    user = await db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return user


async def clear_session(db: AsyncSession, request: Request, response: Response) -> None:
    """Revoke the current session server side and drop the cookie."""
    token = request.cookies.get("session")
    if token:
        await db.execute(
            delete(AuthSession).where(AuthSession.token_hash == hash_token(token))
        )
        await db.commit()
    response.delete_cookie(key="session", path="/")


def require_agent(user: User) -> User:
    if user.role != "agent":
        raise HTTPException(status_code=403, detail="Agent role required")
    return user
