def make_project(client, identity, **overrides):
    payload = {
        "organization_id": str(identity["organization"].id),
        "workspace_id": str(identity["workspace"].id),
        "name": "Phase 4 Project",
    }
    payload.update(overrides)
    response = client.post(
        "/api/v1/projects/", json=payload,
        headers={"Authorization": "Bearer " + identity["token"]},
    )
    return response


def make_event(client, identity, project_id, **overrides):
    payload = {"project_id": str(project_id), "name": "Launch Event"}
    payload.update(overrides)
    return client.post(
        "/api/v1/events/", json=payload,
        headers={"Authorization": "Bearer " + identity["token"]},
    )


def test_project_crud_filters_and_version_conflict(client, identity_factory):
    identity = identity_factory(role="designer")
    created = make_project(client, identity, status="active", budget="1200.50", guest_count=20)
    assert created.status_code == 201
    project = created.json()
    assert project["budget"] == "1200.50"
    assert project["version"] == 1

    listed = client.get(
        "/api/v1/projects/",
        params={"organization_id": str(identity["organization"].id), "status": "active", "limit": 1},
        headers={"Authorization": "Bearer " + identity["token"]},
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [project["id"]]
    assert client.get(
        f"/api/v1/projects/{project['id']}",
        headers={"Authorization": "Bearer " + identity["token"]},
    ).status_code == 200

    updated = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"name": "Updated", "version": 1},
        headers={"Authorization": "Bearer " + identity["token"]},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    stale = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"name": "Stale", "version": 1},
        headers={"Authorization": "Bearer " + identity["token"]},
    )
    assert stale.status_code == 409
    assert make_project(client, identity, status="unknown").status_code == 422
    assert make_project(client, identity, guest_count=-1).status_code == 422
    assert make_project(client, identity, budget="1.001").status_code == 422
    assert client.patch(
        f"/api/v1/projects/{project['id']}", json={"status": "draft", "version": 2},
        headers={"Authorization": "Bearer " + identity["token"]},
    ).status_code == 409


def test_event_schedule_configuration_and_project_lifecycle(client, identity_factory):
    identity = identity_factory(role="designer")
    project = make_project(client, identity).json()
    response = make_event(
        client, identity, project["id"], event_date="2027-04-02",
        starts_at="2027-04-02T10:00:00+05:30", ends_at="2027-04-02T12:00:00+05:30",
        guest_count=50, budget="900.25", currency="inr", status="scheduled",
    )
    assert response.status_code == 201
    event = response.json()
    assert event["project_id"] == project["id"]
    assert event["organization_id"] == project["organization_id"]
    assert event["currency"] == "INR"
    assert event["budget"] == "900.25"
    assert event["version"] == 1

    project_events = client.get(
        f"/api/v1/projects/{project['id']}/events",
        headers={"Authorization": "Bearer " + identity["token"]},
    )
    assert project_events.status_code == 200
    assert [row["id"] for row in project_events.json()] == [event["id"]]
    changed = client.patch(
        f"/api/v1/events/{event['id']}",
        json={"name": "Updated Event", "version": 1},
        headers={"Authorization": "Bearer " + identity["token"]},
    )
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    assert client.patch(
        f"/api/v1/events/{event['id']}", json={"status": "draft", "version": 2},
        headers={"Authorization": "Bearer " + identity["token"]},
    ).status_code == 409
    assert client.patch(
        f"/api/v1/events/{event['id']}", json={"version": 1},
        headers={"Authorization": "Bearer " + identity["token"]},
    ).status_code == 409
    assert client.delete(
        f"/api/v1/events/{event['id']}",
        headers={"Authorization": "Bearer " + identity["token"]},
    ).status_code == 204


def test_event_rejects_invalid_schedules_and_cross_tenant_project(client, identity_factory):
    first = identity_factory(role="designer")
    second = identity_factory(role="designer")
    project = make_project(client, first).json()
    headers = {"Authorization": "Bearer " + first["token"]}
    assert make_event(
        client, first, project["id"], starts_at="2027-01-02T10:00:00Z",
        ends_at="2027-01-02T09:00:00Z",
    ).status_code == 422
    assert make_event(
        client, first, project["id"], starts_at="2027-01-02T10:00:00",
    ).status_code == 422
    assert make_event(
        client, first, project["id"], event_date="2027-01-03",
        starts_at="2027-01-02T10:00:00Z",
    ).status_code == 422
    assert make_event(client, first, project["id"], guest_count=-1).status_code == 422
    assert make_event(client, first, project["id"], budget="-1").status_code == 422
    assert make_event(client, first, project["id"], currency="US").status_code == 422
    assert make_event(client, first, project["id"], status="unknown").status_code == 422
    foreign_project = make_project(client, second).json()
    assert make_event(client, first, foreign_project["id"]).status_code == 404
    assert client.get(
        f"/api/v1/projects/{foreign_project['id']}/events", headers=headers
    ).status_code == 404


def test_project_delete_cascades_associated_events(client, identity_factory):
    identity = identity_factory(role="designer")
    project = make_project(client, identity).json()
    event = make_event(client, identity, project["id"]).json()
    headers = {"Authorization": "Bearer " + identity["token"]}
    assert client.delete(f"/api/v1/projects/{project['id']}", headers=headers).status_code == 204
    assert client.get(f"/api/v1/events/{event['id']}", headers=headers).status_code == 404


def test_event_creation_uses_project_role_and_requires_auth(
    client, unauthenticated_client, identity_factory
):
    owner = identity_factory(role="owner")
    member = identity_factory(role="member", organization=owner["organization"])
    project = make_project(client, owner).json()

    assert make_event(client, member, project["id"]).status_code == 403
    assert unauthenticated_client.post(
        "/api/v1/events/", json={"project_id": str(project["id"]), "name": "No Auth"}
    ).status_code == 401
