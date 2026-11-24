from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth import (
    AuthContext,
    create_session,
    hash_password,
    normalize_email,
    password_hasher,
    verify_password,
)
from app.models.user import User
from app.models.user_session import UserSession
from app.schemas.auth import LoginRequest


def login(db: Session, data: LoginRequest) -> tuple[User, str, datetime]:
    email = normalize_email(data.email)
    user = db.scalars(select(User).where(func.lower(User.email) == email)).first()

    if not verify_password(data.password, user.password_hash if user else None):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(data.password)

    token, session = create_session(db, user)
    db.commit()
    db.refresh(session)
    return user, token, session.expires_at


def refresh(db: Session, context: AuthContext) -> tuple[User, str, datetime]:
    now = datetime.now(timezone.utc)
    revoked = db.execute(
        update(UserSession)
        .where(
            UserSession.id == context.session.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .values(revoked_at=now)
    )
    if revoked.rowcount != 1:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token, session = create_session(db, context.user)
    db.commit()
    db.refresh(session)
    return context.user, token, session.expires_at


def logout(db: Session, context: AuthContext) -> None:
    db.execute(
        update(UserSession)
        .where(
            UserSession.id == context.session.id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    db.commit()
