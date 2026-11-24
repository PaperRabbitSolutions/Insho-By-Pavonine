import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    create_session,
    hash_password,
    hash_token,
    normalize_email,
    password_hasher,
    verify_password,
)
from app.models.organization import Organization
from app.models.organization_invitation import OrganizationInvitation
from app.models.organization_member import OrganizationMember
from app.models.user import User
from app.schemas.auth import AcceptInvitationRequest
from app.schemas.organization import InvitationCreate, OrganizationMemberUpdate
from app.security import ADMIN_ROLES, ROLES


def require_membership(db: Session, user_id: UUID, organization_id: UUID) -> OrganizationMember:
    member = db.scalars(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.organization_id == organization_id,
        )
    ).first()
    if not member or member.role not in ROLES:
        raise HTTPException(status_code=404, detail="Resource not found")
    return member


def lock_organization(db: Session, organization_id: UUID) -> Organization:
    organization = db.scalars(
        select(Organization)
        .where(Organization.id == organization_id)
        .with_for_update()
    ).first()
    if not organization:
        raise HTTPException(status_code=404, detail="Resource not found")
    return organization


def claim_initial_owner(db: Session, user_id: UUID, organization_id: UUID) -> OrganizationMember:
    lock_organization(db, organization_id)
    require_membership(db, user_id, organization_id)
    members = db.scalars(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == organization_id)
        .order_by(
            case((OrganizationMember.role == "admin", 0), else_=1),
            OrganizationMember.created_at,
            OrganizationMember.id,
        )
        .with_for_update()
    ).all()
    if any(member.role == "owner" for member in members):
        raise HTTPException(status_code=409, detail="The organization already has an owner")
    if not members or members[0].user_id != user_id:
        raise HTTPException(status_code=403, detail="Only the initial organization member may claim ownership")
    members[0].role = "owner"
    db.commit()
    db.refresh(members[0])
    return members[0]


def list_members(db: Session, user_id: UUID, organization_id: UUID):
    require_membership(db, user_id, organization_id)
    rows = db.execute(
        select(OrganizationMember, User)
        .join(User, User.id == OrganizationMember.user_id)
        .where(OrganizationMember.organization_id == organization_id)
        .order_by(OrganizationMember.created_at, OrganizationMember.id)
    ).all()
    return [
        {
            "user_id": member.user_id,
            "email": user.email,
            "full_name": user.full_name,
            "role": member.role,
            "created_at": member.created_at,
        }
        for member, user in rows
    ]


def create_invitation(
    db: Session, actor_id: UUID, organization_id: UUID, data: InvitationCreate
) -> tuple[OrganizationInvitation, str]:
    lock_organization(db, organization_id)
    actor = require_membership(db, actor_id, organization_id)
    if actor.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if data.role == "owner":
        raise HTTPException(status_code=422, detail="Owner invitations are not allowed")
    if actor.role == "admin" and data.role == "admin":
        raise HTTPException(status_code=403, detail="Only an owner may invite an admin")

    email = normalize_email(data.email)
    existing_member = db.execute(
        select(OrganizationMember.id)
        .join(User, User.id == OrganizationMember.user_id)
        .where(
            OrganizationMember.organization_id == organization_id,
            func.lower(User.email) == email,
        )
    ).first()
    if existing_member:
        raise HTTPException(status_code=409, detail="User is already a member")

    now = datetime.now(timezone.utc)
    active_invite = db.scalars(
        select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == organization_id,
            OrganizationInvitation.email == email,
            OrganizationInvitation.accepted_at.is_(None),
            OrganizationInvitation.revoked_at.is_(None),
            OrganizationInvitation.expires_at > now,
        )
    ).first()
    if active_invite:
        raise HTTPException(status_code=409, detail="An active invitation already exists")

    raw_token = secrets.token_urlsafe(32)
    invitation = OrganizationInvitation(
        organization_id=organization_id,
        email=email,
        role=data.role,
        token_hash=hash_token(raw_token),
        expires_at=now + timedelta(days=7),
        created_by_user_id=actor_id,
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return invitation, raw_token


def accept_invitation(
    db: Session,
    data: AcceptInvitationRequest,
) -> dict:
    now = datetime.now(timezone.utc)
    invitation = db.scalars(
        select(OrganizationInvitation)
        .where(OrganizationInvitation.token_hash == hash_token(data.token))
        .with_for_update()
    ).first()
    if (
        not invitation
        or invitation.accepted_at
        or invitation.revoked_at
        or invitation.expires_at <= now
    ):
        raise HTTPException(status_code=404, detail="Invitation is invalid or expired")
    if not invitation.created_by_user_id:
        raise HTTPException(status_code=404, detail="Invitation is invalid or expired")
    inviter = db.scalars(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == invitation.organization_id,
            OrganizationMember.user_id == invitation.created_by_user_id,
        )
    ).first()
    if not inviter or inviter.role not in ADMIN_ROLES:
        raise HTTPException(status_code=404, detail="Invitation is invalid or expired")
    if invitation.role == "admin" and inviter.role != "owner":
        raise HTTPException(status_code=404, detail="Invitation is invalid or expired")

    user = db.scalars(
        select(User).where(func.lower(User.email) == invitation.email)
    ).first()
    if user:
        if not user.is_active or not verify_password(data.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid invitation acceptance credentials")
        if password_hasher.check_needs_rehash(user.password_hash):
            user.password_hash = hash_password(data.password)
    else:
        if not data.full_name:
            raise HTTPException(
                status_code=422,
                detail="Full name and a password of at least 12 characters are required",
            )
        if len(data.password) < 12:
            raise HTTPException(status_code=422, detail="Password must contain at least 12 characters")
        if not data.full_name.strip():
            raise HTTPException(status_code=422, detail="Full name cannot be blank")
        user = User(
            email=invitation.email,
            full_name=data.full_name.strip(),
            password_hash=hash_password(data.password),
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Invitation could not be accepted") from exc

    existing_member = db.scalars(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == invitation.organization_id,
            OrganizationMember.user_id == user.id,
        )
    ).first()
    if existing_member:
        raise HTTPException(status_code=409, detail="User is already a member")

    db.add(
        OrganizationMember(
            organization_id=invitation.organization_id,
            user_id=user.id,
            role=invitation.role,
        )
    )
    invitation.accepted_at = now
    access_token, session = create_session(db, user)
    expires_at = session.expires_at
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Invitation could not be accepted") from exc
    db.refresh(user)
    return {
        "organization_id": invitation.organization_id,
        "role": invitation.role,
        "user": user,
        "access_token": access_token,
        "token_type": "bearer",
        "expires_at": expires_at,
    }


def revoke_invitation(
    db: Session, actor_id: UUID, organization_id: UUID, invitation_id: UUID
) -> None:
    lock_organization(db, organization_id)
    actor = require_membership(db, actor_id, organization_id)
    if actor.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    invitation = db.scalars(
        select(OrganizationInvitation).where(
            OrganizationInvitation.id == invitation_id,
            OrganizationInvitation.organization_id == organization_id,
        )
    ).first()
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if actor.role == "admin" and invitation.role == "admin":
        raise HTTPException(status_code=403, detail="Only an owner may revoke an admin invitation")
    if invitation.accepted_at:
        raise HTTPException(status_code=409, detail="Invitation has already been accepted")
    invitation.revoked_at = datetime.now(timezone.utc)
    db.commit()


def update_member(
    db: Session,
    actor_id: UUID,
    organization_id: UUID,
    target_user_id: UUID,
    data: OrganizationMemberUpdate,
) -> OrganizationMember:
    lock_organization(db, organization_id)
    actor = require_membership(db, actor_id, organization_id)
    target = db.scalars(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == target_user_id,
        )
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Membership not found")
    if actor.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if actor.role == "admin" and (
        target.role in {"owner", "admin"} or data.role in {"owner", "admin"}
    ):
        raise HTTPException(status_code=403, detail="Only an owner may manage admins or owners")
    if actor_id == target_user_id and data.role != actor.role:
        raise HTTPException(status_code=403, detail="You cannot change your own role")
    if target.role == "owner" and data.role != "owner":
        owner_count = db.scalar(
            select(func.count()).select_from(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.role == "owner",
            )
        )
        if owner_count <= 1:
            raise HTTPException(status_code=409, detail="The organization must retain an owner")
    target.role = data.role
    db.commit()
    db.refresh(target)
    return target


def remove_member(
    db: Session, actor_id: UUID, organization_id: UUID, target_user_id: UUID
) -> None:
    lock_organization(db, organization_id)
    actor = require_membership(db, actor_id, organization_id)
    target = db.scalars(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == target_user_id,
        )
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Membership not found")
    self_leave = actor_id == target_user_id
    if not self_leave:
        if actor.role not in ADMIN_ROLES:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        if actor.role == "admin" and target.role in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="Only an owner may remove admins or owners")
    if target.role == "owner":
        owner_count = db.scalar(
            select(func.count()).select_from(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.role == "owner",
            )
        )
        if owner_count <= 1:
            raise HTTPException(status_code=409, detail="The organization must retain an owner")
    db.delete(target)
    db.commit()
