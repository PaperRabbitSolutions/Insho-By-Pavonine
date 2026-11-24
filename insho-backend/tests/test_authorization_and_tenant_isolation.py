from app.models.design_dna import DesignDNA
from app.models.event import Event
from app.models.project import Project
from app.models.project_memory import ProjectMemory
from app.models.venue import Venue
from app.models.venue_source import VenueSource
from app.models.venue_zone import VenueZone


def test_members_are_read_only_and_cannot_escalate_their_role(
    client, identity_factory, db_session
):
    owner_id = client.get("/api/v1/auth/me").json()["id"]
    from app.models.organization_member import OrganizationMember
    from app.models.organization import Organization

    owner_membership = db_session.query(OrganizationMember).filter_by(user_id=owner_id).one()
    organization = db_session.get(Organization, owner_membership.organization_id)
    member = identity_factory(role="member", organization=organization)

    project = Project(
        organization_id=owner_membership.organization_id,
        workspace_id=member["workspace"].id,
        name="Member readable project",
    )
    db_session.add(project)
    db_session.commit()

    member_headers = {"Authorization": "Bearer " + member["token"]}
    assert client.get(f"/api/v1/projects/{project.id}", headers=member_headers).status_code == 200
    assert client.patch(
        f"/api/v1/projects/{project.id}",
        json={"name": "Not allowed"},
        headers=member_headers,
    ).status_code == 403
    assert client.patch(
        f"/api/v1/organizations/{owner_membership.organization_id}/members/{member['user'].id}",
        json={"role": "owner"},
        headers=member_headers,
    ).status_code == 403

    assigned = client.patch(
        f"/api/v1/organizations/{owner_membership.organization_id}/members/{member['user'].id}",
        json={"role": "designer"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["role"] == "designer"


def test_admin_cannot_grant_admin_or_manage_owner(client, identity_factory, db_session):
    from app.models.organization_member import OrganizationMember
    from app.models.organization import Organization

    owner_id = client.get("/api/v1/auth/me").json()["id"]
    owner_membership = db_session.query(OrganizationMember).filter_by(user_id=owner_id).one()
    organization = db_session.get(Organization, owner_membership.organization_id)
    admin = identity_factory(role="admin", organization=organization)
    target = identity_factory(role="member", organization=organization)
    admin_headers = {"Authorization": "Bearer " + admin["token"]}

    self_promotion = client.patch(
        f"/api/v1/organizations/{owner_membership.organization_id}/members/{admin['user'].id}",
        json={"role": "owner"},
        headers=admin_headers,
    )
    promote_peer = client.patch(
        f"/api/v1/organizations/{owner_membership.organization_id}/members/{target['user'].id}",
        json={"role": "admin"},
        headers=admin_headers,
    )
    remove_owner = client.delete(
        f"/api/v1/organizations/{owner_membership.organization_id}/members/{owner_id}",
        headers=admin_headers,
    )
    assert self_promotion.status_code == 403
    assert promote_peer.status_code == 403
    assert remove_owner.status_code == 403
    admin_invite = client.post(
        f"/api/v1/organizations/{owner_membership.organization_id}/invitations",
        json={"email": "admin-invite@example.test", "role": "admin"},
        headers=admin_headers,
    )
    designer_invite = client.post(
        f"/api/v1/organizations/{owner_membership.organization_id}/invitations",
        json={"email": "designer-invite@example.test", "role": "designer"},
        headers=admin_headers,
    )
    assert admin_invite.status_code == 403
    assert designer_invite.status_code == 201


def test_last_owner_cannot_demote_or_remove_self(client, db_session):
    from app.models.organization_member import OrganizationMember

    owner_id = client.get("/api/v1/auth/me").json()["id"]
    membership = db_session.query(OrganizationMember).filter_by(user_id=owner_id).one()
    demote = client.patch(
        f"/api/v1/organizations/{membership.organization_id}/members/{owner_id}",
        json={"role": "member"},
    )
    leave = client.delete(
        f"/api/v1/organizations/{membership.organization_id}/members/{owner_id}"
    )
    assert demote.status_code == 403
    assert leave.status_code == 409


def test_organization_admin_cannot_mutate_global_event_type_catalog(
    client, identity_factory
):
    admin = identity_factory(role="owner")
    headers = {"Authorization": "Bearer " + admin["token"]}
    assert client.get("/api/v1/event-types/", headers=headers).status_code == 200
    denied = client.post(
        "/api/v1/event-types/",
        json={"name": "Tenant cannot publish global type"},
        headers=headers,
    )
    assert denied.status_code == 403


def test_cross_tenant_resources_are_hidden_and_immutable(
    client, identity_factory, db_session
):
    user_a = identity_factory(role="designer")
    user_b = identity_factory(role="designer")
    project_a = Project(
        organization_id=user_a["organization"].id,
        workspace_id=user_a["workspace"].id,
        name="A project",
    )
    project_b = Project(
        organization_id=user_b["organization"].id,
        workspace_id=user_b["workspace"].id,
        name="B project",
    )
    venue_a = Venue(organization_id=user_a["organization"].id, name="A venue")
    venue_b = Venue(organization_id=user_b["organization"].id, name="B venue")
    event_a = Event(organization_id=user_a["organization"].id, name="A event")
    event_b = Event(organization_id=user_b["organization"].id, name="B event")
    db_session.add_all([project_a, project_b, venue_a, venue_b, event_a, event_b])
    db_session.flush()
    zone_a = VenueZone(venue_id=venue_a.id, name="A zone")
    source_a = VenueSource(venue_id=venue_a.id, source_type="plan")
    dna_a = DesignDNA(project_id=project_a.id, mood="A mood")
    memory_a = ProjectMemory(project_id=project_a.id, memory_data={"private": True})
    zone_b = VenueZone(venue_id=venue_b.id, name="B zone")
    source_b = VenueSource(venue_id=venue_b.id, source_type="plan")
    dna_b = DesignDNA(project_id=project_b.id, mood="B mood")
    memory_b = ProjectMemory(project_id=project_b.id, memory_data={"private": True})
    db_session.add_all([zone_a, source_a, dna_a, memory_a, zone_b, source_b, dna_b, memory_b])
    db_session.commit()

    headers_a = {"Authorization": "Bearer " + user_a["token"]}
    headers_b = {"Authorization": "Bearer " + user_b["token"]}

    # A cannot discover B's organization or any direct/nested resources by ID.
    assert client.get(
        "/api/v1/projects/", params={"organization_id": str(user_b["organization"].id)}, headers=headers_a
    ).status_code == 404
    assert client.post(
        "/api/v1/projects/",
        json={
            "organization_id": str(user_b["organization"].id),
            "workspace_id": str(user_b["workspace"].id),
            "name": "cross tenant",
        },
        headers=headers_a,
    ).status_code == 404
    assert client.get(
        f"/api/v1/organizations/{user_b['organization'].id}/members",
        headers=headers_a,
    ).status_code == 404
    assert client.post(
        f"/api/v1/organizations/{user_b['organization'].id}/invitations",
        json={"email": "cross-tenant@example.test", "role": "member"},
        headers=headers_a,
    ).status_code == 404

    hidden_resources = [
        ("GET", f"/api/v1/projects/{project_b.id}", None),
        ("PATCH", f"/api/v1/projects/{project_b.id}", {"name": "hacked"}),
        ("DELETE", f"/api/v1/projects/{project_b.id}", None),
        ("GET", f"/api/v1/events/{event_b.id}", None),
        ("PATCH", f"/api/v1/events/{event_b.id}", {"name": "hacked"}),
        ("DELETE", f"/api/v1/events/{event_b.id}", None),
        ("GET", f"/api/v1/venues/{venue_b.id}", None),
        ("PATCH", f"/api/v1/venues/{venue_b.id}", {"name": "hacked"}),
        ("DELETE", f"/api/v1/venues/{venue_b.id}", None),
        ("GET", f"/api/v1/venue-zones/{zone_b.id}", None),
        ("PATCH", f"/api/v1/venue-zones/{zone_b.id}", {"name": "hacked"}),
        ("DELETE", f"/api/v1/venue-zones/{zone_b.id}", None),
        ("GET", f"/api/v1/venue-sources/{source_b.id}", None),
        ("PATCH", f"/api/v1/venue-sources/{source_b.id}", {"source_type": "hacked"}),
        ("DELETE", f"/api/v1/venue-sources/{source_b.id}", None),
        ("GET", f"/api/v1/design-dna/{dna_b.id}", None),
        ("PATCH", f"/api/v1/design-dna/{dna_b.id}", {"mood": "hacked"}),
        ("DELETE", f"/api/v1/design-dna/{dna_b.id}", None),
        ("GET", f"/api/v1/project-memory/{memory_b.id}", None),
        ("PATCH", f"/api/v1/project-memory/{memory_b.id}", {"summary": "hacked"}),
        ("DELETE", f"/api/v1/project-memory/{memory_b.id}", None),
        ("GET", f"/api/v1/design-dna/project/{project_b.id}", None),
        ("GET", f"/api/v1/project-memory/project/{project_b.id}", None),
    ]
    for method, path, payload in hidden_resources:
        response = client.request(method, path, json=payload, headers=headers_a)
        assert response.status_code == 404, f"{method} {path}: {response.status_code}"

    assert client.get(
        "/api/v1/events/", params={"organization_id": str(user_b["organization"].id)}, headers=headers_a
    ).status_code == 404
    assert client.get(
        "/api/v1/venues/", params={"organization_id": str(user_b["organization"].id)}, headers=headers_a
    ).status_code == 404
    assert client.get(
        f"/api/v1/venue-zones/?venue_id={venue_b.id}", headers=headers_a
    ).status_code == 404
    assert client.get(
        f"/api/v1/venue-sources/?venue_id={venue_b.id}", headers=headers_a
    ).status_code == 404
    assert client.post(
        "/api/v1/venue-zones/", json={"venue_id": str(venue_b.id), "name": "Nope"}, headers=headers_a
    ).status_code == 404
    assert client.post(
        "/api/v1/venue-sources/",
        json={"venue_id": str(venue_b.id), "source_type": "plan"},
        headers=headers_a,
    ).status_code == 404
    assert client.post(
        "/api/v1/design-dna/", json={"project_id": str(project_b.id)}, headers=headers_a
    ).status_code == 404
    assert client.post(
        "/api/v1/project-memory/", json={"project_id": str(project_b.id)}, headers=headers_a
    ).status_code == 404
    assert client.post(
        "/api/v1/events/",
        json={"project_id": str(project_b.id), "name": "Nope"},
        headers=headers_a,
    ).status_code == 404
    assert client.post(
        "/api/v1/venues/",
        json={"organization_id": str(user_b["organization"].id), "name": "Nope"},
        headers=headers_a,
    ).status_code == 404

    # B is equally unable to read, change, or delete A's tenant resources.
    for path, payload in [
        (f"/api/v1/projects/{project_a.id}", {"name": "hacked"}),
        (f"/api/v1/events/{event_a.id}", {"name": "hacked"}),
        (f"/api/v1/venues/{venue_a.id}", {"name": "hacked"}),
        (f"/api/v1/venue-zones/{zone_a.id}", {"name": "hacked"}),
        (f"/api/v1/venue-sources/{source_a.id}", {"source_type": "hacked"}),
        (f"/api/v1/design-dna/{dna_a.id}", {"mood": "hacked"}),
        (f"/api/v1/project-memory/{memory_a.id}", {"summary": "hacked"}),
    ]:
        assert client.get(path, headers=headers_b).status_code == 404
        assert client.patch(path, json=payload, headers=headers_b).status_code == 404
        assert client.delete(path, headers=headers_b).status_code == 404
    assert client.get(f"/api/v1/design-dna/project/{project_a.id}", headers=headers_b).status_code == 404
    assert client.get(f"/api/v1/project-memory/project/{project_a.id}", headers=headers_b).status_code == 404
    assert client.get(f"/api/v1/venue-zones/?venue_id={venue_a.id}", headers=headers_b).status_code == 404
    assert client.get(f"/api/v1/venue-sources/?venue_id={venue_a.id}", headers=headers_b).status_code == 404

    # Both users can access their own tenant resources.
    assert client.get(f"/api/v1/projects/{project_a.id}", headers=headers_a).status_code == 200
    assert client.get(f"/api/v1/projects/{project_b.id}", headers=headers_b).status_code == 200
    assert client.get(f"/api/v1/venues/{venue_b.id}", headers=headers_b).status_code == 200
    assert client.get(f"/api/v1/events/{event_a.id}", headers=headers_a).status_code == 200
    assert client.patch(
        f"/api/v1/projects/{project_a.id}",
        json={"name": "A designer can edit"},
        headers=headers_a,
    ).status_code == 200
