from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.models.organization_member import OrganizationMember
from app.schemas.organization import (
    InvitationCreate,
    InvitationCreatedResponse,
    OrganizationMemberResponse,
    OrganizationMemberUpdate,
)
from app.services.organization_access import (
    create_invitation,
    claim_initial_owner,
    list_members,
    remove_member,
    revoke_invitation,
    update_member,
)

router = APIRouter(prefix="/organizations", tags=["Organization Access"])


@router.get("/mine")
def my_organizations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Organization.id, Organization.name, OrganizationMember.role)
        .join(OrganizationMember, OrganizationMember.organization_id == Organization.id)
        .where(OrganizationMember.user_id == current_user.id)
        .order_by(Organization.name, Organization.id)
    ).all()
    return [dict(row._mapping) for row in rows]


@router.post("/{organization_id}/claim-owner", response_model=OrganizationMemberResponse)
def claim_owner(
    organization_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    member = claim_initial_owner(db, current_user.id, organization_id)
    return next(
        item
        for item in list_members(db, current_user.id, organization_id)
        if item["user_id"] == member.user_id
    )


@router.get("/{organization_id}/members", response_model=list[OrganizationMemberResponse])
def get_members(
    organization_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_members(db, current_user.id, organization_id)


@router.patch(
    "/{organization_id}/members/{user_id}",
    response_model=OrganizationMemberResponse,
)
def change_member_role(
    organization_id: UUID,
    user_id: UUID,
    data: OrganizationMemberUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    member = update_member(db, current_user.id, organization_id, user_id, data)
    return next(
        item
        for item in list_members(db, current_user.id, organization_id)
        if item["user_id"] == member.user_id
    )


@router.delete(
    "/{organization_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_member(
    organization_id: UUID,
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    remove_member(db, current_user.id, organization_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{organization_id}/invitations",
    response_model=InvitationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def invite_member(
    organization_id: UUID,
    data: InvitationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invitation, token = create_invitation(db, current_user.id, organization_id, data)
    return {
        "id": invitation.id,
        "organization_id": invitation.organization_id,
        "email": invitation.email,
        "role": invitation.role,
        "expires_at": invitation.expires_at,
        "token": token,
    }


@router.delete(
    "/{organization_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_invitation(
    organization_id: UUID,
    invitation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    revoke_invitation(db, current_user.id, organization_id, invitation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
