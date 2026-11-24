from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.organization_member import OrganizationMember


def test_only_initial_member_can_claim_owner_and_owner_is_retained(
    client, identity_factory, db_session
):
    first = identity_factory(role="member")
    second = identity_factory(role="member", organization=first["organization"])
    first_member = db_session.scalars(
        select(OrganizationMember).where(OrganizationMember.user_id == first["user"].id)
    ).one()
    first_member.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()
    path = f"/api/v1/organizations/{first['organization'].id}/claim-owner"

    cannot_claim = client.post(path, headers={"Authorization": "Bearer " + second["token"]})
    claimed = client.post(path, headers={"Authorization": "Bearer " + first["token"]})
    claimed_again = client.post(path, headers={"Authorization": "Bearer " + first["token"]})

    assert cannot_claim.status_code == 403
    assert claimed.status_code == 200
    assert claimed.json()["role"] == "owner"
    assert claimed_again.status_code == 409


def test_member_cannot_invite_or_manage_other_members(client, identity_factory, db_session):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    owner_membership = db_session.scalars(
        select(OrganizationMember).where(OrganizationMember.user_id == owner_id)
    ).one()
    from app.models.organization import Organization

    organization = db_session.get(Organization, owner_membership.organization_id)
    member = identity_factory(role="member", organization=organization)
    headers = {"Authorization": "Bearer " + member["token"]}
    invitation = client.post(
        f"/api/v1/organizations/{organization.id}/invitations",
        json={"email": "member-invite@example.test", "role": "member"},
        headers=headers,
    )
    removal = client.delete(
        f"/api/v1/organizations/{organization.id}/members/{owner_id}",
        headers=headers,
    )
    assert invitation.status_code == 403
    assert removal.status_code == 403
