def create_project(client, identity, name="Graph Project"):
    response = client.post(
        "/api/v1/projects/",
        json={
            "organization_id": str(identity["organization"].id),
            "workspace_id": str(identity["workspace"].id),
            "name": name,
        },
        headers={"Authorization": "Bearer " + identity["token"]},
    )
    assert response.status_code == 201
    return response.json()


def create_venue(client, identity, name="Context Venue"):
    response = client.post(
        "/api/v1/venues/",
        json={
            "organization_id": str(identity["organization"].id),
            "name": name,
        },
        headers={"Authorization": "Bearer " + identity["token"]},
    )
    assert response.status_code == 201
    return response.json()


def create_event(client, identity, project_id, **fields):
    payload = {"project_id": str(project_id), "name": "Context Event"}
    payload.update(fields)
    return client.post(
        "/api/v1/events/",
        json=payload,
        headers={"Authorization": "Bearer " + identity["token"]},
    )


def test_memory_items_keep_structure_provenance_and_project_event_scope(
    client, identity_factory
):
    identity = identity_factory(role="designer")
    other = identity_factory(role="designer")
    project = create_project(client, identity)
    other_project = create_project(client, other, "Other Project")
    event = create_event(client, identity, project["id"]).json()
    other_event = create_event(client, other, other_project["id"]).json()
    headers = {"Authorization": "Bearer " + identity["token"]}

    payload = {
        "project_id": project["id"],
        "event_id": event["id"],
        "kind": "preference",
        "title": "Floral direction",
        "content": {"palette": ["ivory", "sage"], "priority": "high"},
        "source_type": "client_interview",
        "source_reference": "interview-2027-01",
    }
    created = client.post("/api/v1/project-memory/items", json=payload, headers=headers)
    assert created.status_code == 201
    item = created.json()
    assert item["created_by_user_id"] == str(identity["user"].id)
    assert item["source_reference"] == "interview-2027-01"
    assert item["content"]["palette"] == ["ivory", "sage"]
    assert item["status"] == "active"

    assert client.post(
        "/api/v1/project-memory/items",
        json={**payload, "event_id": other_event["id"]}, headers=headers,
    ).status_code == 404
    assert client.post(
        "/api/v1/project-memory/items",
        json={**payload, "content": {}}, headers=headers,
    ).status_code == 422

    listing = client.get(
        f"/api/v1/project-memory/project/{project['id']}/items", headers=headers
    )
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == [item["id"]]

    archived = client.patch(
        f"/api/v1/project-memory/items/{item['id']}",
        json={"status": "archived"}, headers=headers,
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert client.get(
        f"/api/v1/project-memory/project/{project['id']}/items", headers=headers
    ).json() == []
    assert len(client.get(
        f"/api/v1/project-memory/project/{project['id']}/items?include_archived=true",
        headers=headers,
    ).json()) == 1

    assert client.get(
        f"/api/v1/project-memory/items/{item['id']}",
        headers={"Authorization": "Bearer " + other["token"]},
    ).status_code == 404
    assert client.delete(
        f"/api/v1/events/{event['id']}", headers=headers
    ).status_code == 204
    unlinked = client.get(f"/api/v1/project-memory/items/{item['id']}", headers=headers)
    assert unlinked.status_code == 200
    assert unlinked.json()["event_id"] is None


def test_context_reads_resolve_project_event_venue_and_zones(client, identity_factory):
    identity = identity_factory(role="designer")
    other = identity_factory(role="designer")
    project = create_project(client, identity)
    venue = create_venue(client, identity)
    other_venue = create_venue(client, other, "Private Venue")
    headers = {"Authorization": "Bearer " + identity["token"]}

    zone_response = client.post(
        "/api/v1/venue-zones/",
        json={"venue_id": venue["id"], "name": "Garden"}, headers=headers,
    )
    assert zone_response.status_code == 201
    event_response = create_event(client, identity, project["id"], venue_id=venue["id"])
    assert event_response.status_code == 201
    event = event_response.json()
    assert event["venue_id"] == venue["id"]

    assert create_event(
        client, identity, project["id"], venue_id=other_venue["id"]
    ).status_code == 404
    assert client.patch(
        f"/api/v1/events/{event['id']}",
        json={"venue_id": other_venue["id"]}, headers=headers,
    ).status_code == 404

    dna = client.post(
        "/api/v1/design-dna/",
        json={"project_id": project["id"], "mood": "botanical"}, headers=headers,
    )
    assert dna.status_code == 201
    legacy_memory = client.post(
        "/api/v1/project-memory/",
        json={"project_id": project["id"], "memory_data": {"palette": ["sage"]}},
        headers=headers,
    )
    assert legacy_memory.status_code == 201
    memory_item = client.post(
        "/api/v1/project-memory/items",
        json={
            "project_id": project["id"], "event_id": event["id"],
            "kind": "decision", "title": "Venue layout",
            "content": {"plan": "garden ceremony"},
        },
        headers=headers,
    )
    assert memory_item.status_code == 201

    project_context = client.get(f"/api/v1/context/projects/{project['id']}", headers=headers)
    assert project_context.status_code == 200
    assert project_context.json()["events"][0]["venue"]["id"] == venue["id"]
    assert project_context.json()["events"][0]["venue_zones"][0]["name"] == "Garden"
    assert project_context.json()["design_dna"]["mood"] == "botanical"

    event_context = client.get(f"/api/v1/context/events/{event['id']}", headers=headers)
    assert event_context.status_code == 200
    assert event_context.json()["project_memory"]["id"] == legacy_memory.json()["id"]
    assert [row["id"] for row in event_context.json()["memory_items"]] == [memory_item.json()["id"]]

    venue_context = client.get(f"/api/v1/context/venues/{venue['id']}", headers=headers)
    assert venue_context.status_code == 200
    assert [row["id"] for row in venue_context.json()["events"]] == [event["id"]]
    assert [row["id"] for row in venue_context.json()["projects"]] == [project["id"]]
    assert [row["id"] for row in venue_context.json()["memory_items"]] == [memory_item.json()["id"]]

    assert client.get(
        f"/api/v1/context/projects/{project['id']}",
        headers={"Authorization": "Bearer " + other["token"]},
    ).status_code == 404

    assert client.delete(f"/api/v1/venues/{venue['id']}", headers=headers).status_code == 204
    after_delete = client.get(f"/api/v1/context/events/{event['id']}", headers=headers)
    assert after_delete.status_code == 200
    assert after_delete.json()["venue"] is None
    assert after_delete.json()["venue_zones"] == []
