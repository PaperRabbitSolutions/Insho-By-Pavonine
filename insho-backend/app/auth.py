import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user import User
from app.models.user_session import UserSession


password_hasher = PasswordHasher()
bearer_scheme = HTTPBearer(auto_error=False)
_dummy_password_hash: Optional[str] = None


@dataclass
class AuthContext:
    user: User
    session: UserSession


def session_ttl_minutes() -> int:
    try:
        minutes = int(os.getenv("AUTH_SESSION_TTL_MINUTES", "30"))
    except ValueError as exc:
        raise RuntimeError("AUTH_SESSION_TTL_MINUTES must be an integer") from exc
    if not 5 <= minutes <= 1440:
        raise RuntimeError("AUTH_SESSION_TTL_MINUTES must be between 5 and 1440")
    return minutes


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def _get_dummy_password_hash() -> str:
    global _dummy_password_hash
    if _dummy_password_hash is None:
        _dummy_password_hash = hash_password(secrets.token_urlsafe(32))
    return _dummy_password_hash


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    try:
        password_hasher.verify(password_hash or _get_dummy_password_hash(), password)
        return password_hash is not None
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=session_ttl_minutes())
    session = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=expires_at,
    )
    db.add(session)
    db.flush()
    return token, session


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def resolve_auth_context(token: str, db: Session) -> AuthContext:
    session = db.scalars(
        select(UserSession).where(UserSession.token_hash == hash_token(token))
    ).first()
    now = datetime.now(timezone.utc)
    if not session or session.revoked_at or session.expires_at <= now:
        raise _invalid_credentials()

    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        raise _invalid_credentials()
    return AuthContext(user=user, session=session)


def get_auth_context(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthContext:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise _invalid_credentials()
    return resolve_auth_context(credentials.credentials, db)


def get_current_user(context: AuthContext = Depends(get_auth_context)) -> User:
    return context.user
