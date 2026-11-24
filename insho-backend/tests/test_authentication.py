from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.user_session import UserSession
from app.auth import hash_token


def test_login_returns_opaque_bearer_and_safe_profile(client, identity_factory, db_session):
    identity = identity_factory(password="correct horse battery staple")

    response = client.post(
        "/api/v1/auth/login",
        json={"email": identity["user"].email.upper(), "password": "correct horse battery staple"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) >= 40
    assert body["user"]["email"] == identity["user"].email
    assert "password_hash" not in body["user"]
    assert "password" not in body["user"]
    assert "is_system_admin" not in body["user"]
    session = db_session.scalars(
        select(UserSession).where(
            UserSession.token_hash == hash_token(body["access_token"])
        )
    ).one()
    assert session.token_hash != body["access_token"]
    assert client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer " + body["access_token"]},
    ).status_code == 200


def test_unknown_user_and_wrong_password_have_same_safe_error(client, identity_factory):
    identity = identity_factory(password="correct horse battery staple")
    wrong = client.post(
        "/api/v1/auth/login",
        json={"email": identity["user"].email, "password": "wrong-password"},
    )
    unknown = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.test", "password": "wrong-password"},
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_malformed_login_is_rejected(client):
    response = client.post("/api/v1/auth/login", json={"email": "bad", "password": "x"})
    assert response.status_code == 422


def test_missing_malformed_expired_and_revoked_tokens_are_rejected(
    unauthenticated_client, identity_factory, db_session
):
    identity = identity_factory()
    assert unauthenticated_client.get("/api/v1/auth/me").status_code == 401
    assert unauthenticated_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-session"}
    ).status_code == 401

    session = db_session.scalars(
        select(UserSession).where(UserSession.token_hash == hash_token(identity["token"]))
    ).one()
    session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert unauthenticated_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer " + identity["token"]}
    ).status_code == 401


def test_refresh_rotates_and_logout_revokes_session(client):
    original_token = client.headers["Authorization"].split(" ", 1)[1]
    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    rotated_token = refreshed.json()["access_token"]
    assert rotated_token != original_token
    assert client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer " + original_token}
    ).status_code == 401

    client.headers["Authorization"] = "Bearer " + rotated_token
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_inactive_user_cannot_use_existing_session(client, identity_factory, db_session):
    identity = identity_factory()
    identity["user"].is_active = False
    db_session.commit()
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer " + identity["token"]},
    )
    assert response.status_code == 401


def test_protected_resource_requires_authentication(unauthenticated_client):
    response = unauthenticated_client.get(
        "/api/v1/projects/00000000-0000-0000-0000-000000000001"
    )
    assert response.status_code == 401


def test_invitation_creates_user_and_session(client, unauthenticated_client, db_session):
    from app.models.organization_member import OrganizationMember
    from app.models.user import User

    owner_id = client.get("/api/v1/auth/me").json()["id"]
    owner_membership = db_session.scalars(
        select(OrganizationMember).where(OrganizationMember.user_id == owner_id)
    ).one()
    invitation = client.post(
        f"/api/v1/organizations/{owner_membership.organization_id}/invitations",
        json={"email": "invited@example.test", "role": "designer"},
    )
    assert invitation.status_code == 201
    assert "token_hash" not in invitation.json()

    accepted = unauthenticated_client.post(
        "/api/v1/auth/accept-invitation",
        json={
            "token": invitation.json()["token"],
            "full_name": "Invited Designer",
            "password": "a-long-secure-password",
        },
    )
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["role"] == "designer"
    assert body["user"]["email"] == "invited@example.test"
    assert body["access_token"]
    created_user = db_session.scalars(
        select(User).where(User.email == "invited@example.test")
    ).one()
    assert created_user.password_hash != "a-long-secure-password"
    assert "password_hash" not in body["user"]


def test_invitation_token_is_single_use(client, unauthenticated_client, db_session):
    from app.models.organization_member import OrganizationMember

    owner_id = client.get("/api/v1/auth/me").json()["id"]
    membership = db_session.scalars(
        select(OrganizationMember).where(OrganizationMember.user_id == owner_id)
    ).one()
    invitation = client.post(
        f"/api/v1/organizations/{membership.organization_id}/invitations",
        json={"email": "once@example.test", "role": "member"},
    ).json()
    payload = {
        "token": invitation["token"],
        "full_name": "Single Use",
        "password": "a-long-secure-password",
    }
    assert unauthenticated_client.post("/api/v1/auth/accept-invitation", json=payload).status_code == 200
    assert unauthenticated_client.post("/api/v1/auth/accept-invitation", json=payload).status_code == 404


def test_existing_invited_user_must_prove_account_password(
    client, identity_factory, db_session
):
    from app.models.organization_member import OrganizationMember

    invited_user = identity_factory(role="member", password="invited-user-password")
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    owner_membership = db_session.scalars(
        select(OrganizationMember).where(OrganizationMember.user_id == owner_id)
    ).one()
    response = client.post(
        f"/api/v1/organizations/{owner_membership.organization_id}/invitations",
        json={"email": invited_user["user"].email, "role": "designer"},
    )
    token = response.json()["token"]

    wrong_account = client.post(
        "/api/v1/auth/accept-invitation",
        json={"token": token, "password": "not-the-invited-password"},
    )
    assert wrong_account.status_code == 401

    accepted = client.post(
        "/api/v1/auth/accept-invitation",
        json={"token": token, "password": "invited-user-password"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["user"]["id"] == str(invited_user["user"].id)
    assert accepted.json()["role"] == "designer"


def test_expired_and_revoked_invitations_cannot_be_accepted(
    client, unauthenticated_client, db_session
):
    from app.models.organization_invitation import OrganizationInvitation
    from app.models.organization_member import OrganizationMember

    owner_id = client.get("/api/v1/auth/me").json()["id"]
    membership = db_session.scalars(
        select(OrganizationMember).where(OrganizationMember.user_id == owner_id)
    ).one()
    expired = client.post(
        f"/api/v1/organizations/{membership.organization_id}/invitations",
        json={"email": "expired@example.test", "role": "member"},
    ).json()
    revoked = client.post(
        f"/api/v1/organizations/{membership.organization_id}/invitations",
        json={"email": "revoked@example.test", "role": "member"},
    ).json()
    expired_row = db_session.scalars(
        select(OrganizationInvitation).where(
            OrganizationInvitation.id == expired["id"]
        )
    ).one()
    revoked_row = db_session.scalars(
        select(OrganizationInvitation).where(
            OrganizationInvitation.id == revoked["id"]
        )
    ).one()
    expired_row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    revoked_row.revoked_at = datetime.now(timezone.utc)
    db_session.commit()

    for token in (expired["token"], revoked["token"]):
        response = unauthenticated_client.post(
            "/api/v1/auth/accept-invitation",
            json={
                "token": token,
                "full_name": "Not Created",
                "password": "a-long-secure-password",
            },
        )
        assert response.status_code == 404


def test_invitation_is_invalid_after_creator_loses_membership(
    client, unauthenticated_client, identity_factory, db_session
):
    from app.models.organization import Organization
    from app.models.organization_member import OrganizationMember

    owner_id = client.get("/api/v1/auth/me").json()["id"]
    owner_member = db_session.scalars(
        select(OrganizationMember).where(OrganizationMember.user_id == owner_id)
    ).one()
    organization = db_session.get(Organization, owner_member.organization_id)
    admin = identity_factory(role="admin", organization=organization)
    invitation = client.post(
        f"/api/v1/organizations/{organization.id}/invitations",
        json={"email": "orphaned-invite@example.test", "role": "designer"},
        headers={"Authorization": "Bearer " + admin["token"]},
    ).json()
    removed = client.delete(
        f"/api/v1/organizations/{organization.id}/members/{admin['user'].id}"
    )
    assert removed.status_code == 204

    response = unauthenticated_client.post(
        "/api/v1/auth/accept-invitation",
        json={
            "token": invitation["token"],
            "full_name": "Orphaned Invite",
            "password": "a-long-secure-password",
        },
    )
    assert response.status_code == 404
