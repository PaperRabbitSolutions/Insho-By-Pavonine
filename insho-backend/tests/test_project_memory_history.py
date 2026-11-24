from uuid import uuid4

import pytest

from app.models.ai_execution import AIExecution
from test_assets import auth
from test_design_versioning import production, versions
from test_phase9_spatial import make_project
from test_production import request
from test_phase5_context_and_memory import create_event


CATEGORIES = (
    "must_have", "must_avoid", "approved", "rejected", "client_preferences", "design_dna", "budget", "venue_dimensions",
    "guest_count", "previous_decisions", "vendor_constraints", "production_constraints", "safety_constraints",
)


def make_memory(versions, category="must_have", status=201, **fields):
    return request(versions.api.client, "POST", "/api/v1/project-memory/items", status, json={
        "project_id": str(versions.project.id), "event_id": str(versions.event.id), "category": category,
        "title": "Field note", "content": {"note": "Authored fixture"}, **fields,
    })


def path(item):
    return f"/api/v1/project-memory/items/{item['id']}"


@pytest.mark.parametrize("category", CATEGORIES)
def test_constitution_memory_categories_are_createable_and_filterable(versions, category):
    row = make_memory(versions, category, source_reference="Test project note")
    assert row["category"] == category and row["verification"] == "unverified" and row["version"] == 1
    assert request(versions.api.client, "GET", path(row))["source_reference"] == "Test project note"
    collection = f"/api/v1/project-memory/project/{versions.project.id}/items"
    assert [item["id"] for item in request(versions.api.client, "GET", collection, params={"category": category})] == [row["id"]]


def test_memory_history_preserves_values_sources_and_active_context(versions):
    api = versions.api
    version = versions.save()
    row = make_memory(versions, "guest_count", content={"count": 180}, source_type="client_interview",
                      source_reference="Workshop notes", linked_version_id=version["id"])
    assert row["linked_version_id"] == version["id"]
    saved = versions.save(version["id"], selection={"memory_item_ids": [row["id"]]})
    update = request(api.client, "PATCH", path(row), json={"expected_version": 1, "content": {"count": 200},
                                                         "reason": "Client increased the invitation count"})
    assert update["version"] == 2
    assert versions.read(saved)["state"]["memory"][row["id"]]["content"]["count"] == 180
    request(api.client, "PATCH", path(row), 409, json={"expected_version": 1, "content": {"count": 260}})
    request(api.client, "PATCH", path(row), 409, json={"source_reference": "Unrelated source"})
    request(api.client, "PATCH", path(row), 409, json={"linked_version_id": saved["id"]})
    request(api.client, "PATCH", path(row), 422, json={"content": {"count": -1}})
    make_memory(versions, "budget", status=422, content={"amount": "20"})
    archived = request(api.client, "POST", path(row) + "/archive", json={"expected_version": 2, "reason": "Replaced by final count"})
    assert archived["status"] == "archived" and archived["version"] == 3 and archived["archived_at"]
    request(api.client, "PATCH", path(row), 409, json={"status": "active"})
    history = request(api.client, "GET", path(row) + "/history")
    assert [item["version"] for item in history] == [3, 2, 1]
    assert [item["state"]["content"]["count"] for item in history] == [200, 200, 180]
    assert all(item["state"]["source_reference"] == "Workshop notes" for item in history)
    ctx = request(api.client, "GET", f"/api/v1/context/events/{versions.event.id}")
    assert ctx["memory_items"] == []
    audit = versions.call("GET", "/design-activity", params={"version_id": version["id"]})
    assert {item["action"] for item in audit} == {"version_created", "memory_created", "memory_updated", "memory_archived"}


def test_memory_permissions_outcomes_and_agent_attribution(versions, db_session, identity_factory):
    api, project = versions.api, versions.project
    run = AIExecution(id=uuid4(), request_id=uuid4(), organization_id=project.organization_id, workspace_id=project.workspace_id,
                      project_id=project.id, user_id=versions.owner["user"].id, task="design_plan", feature="insho_plan",
                      status="completed", output="private output", metadata_json={"provenance": {"event_scope": str(versions.event.id)}})
    db_session.add(run)
    db_session.commit()
    memory = make_memory(versions, "rejected", execution_id=str(run.id), source_reference="Reviewed draft",
                         source_type="ai_suggested")
    assert memory["execution_id"] == str(run.id)
    assert versions.call("GET", "/design-activity")[0]["execution_id"] == str(run.id)
    request(api.client, "PATCH", path(memory), 409, json={"category": "approved"})
    request(api.client, "PATCH", path(memory), 409, json={"verification": "verified"})
    member = identity_factory(role="member", organization=versions.owner["organization"])
    outsider = identity_factory()
    request(api.client, "PATCH", path(memory), 403, headers=auth(member), json={"content": {"note": "Edit"}})
    request(api.client, "GET", path(memory) + "/history", 404, headers=auth(outsider))
    request(api.client, "GET", f"/api/v1/project-memory/project/{project.id}/items", 404, headers=auth(outsider),
            params={"category": "rejected"})
    sibling = make_project(db_session, identity_factory(organization=versions.owner["organization"]))
    make_memory(versions, status=404, project_id=str(sibling.id), event_id=None, execution_id=str(run.id))
    designer = identity_factory(role="designer", organization=versions.owner["organization"])
    api.client.headers.update(auth(designer))
    make_memory(versions, "venue_dimensions", status=403, verification="verified")


def test_event_deletion_archives_scoped_memory_without_releasing_it_to_project(versions):
    api = versions.api
    event = create_event(api.client, versions.owner, str(versions.project.id)).json()
    row = make_memory(versions, "safety_constraints", event_id=event["id"], content={"note": "Keep the service gate clear"})
    general = make_memory(versions, "safety_constraints", event_id=None)
    request(api.client, "DELETE", f"/api/v1/events/{event['id']}", 204)
    detached = request(api.client, "GET", path(row))
    assert detached["status"] == "archived" and detached["event_id"] is None
    revision = request(api.client, "GET", path(row) + "/history")
    assert revision[0]["action"] == "event_detached" and revision[1]["state"]["event_id"] == event["id"]
    active = request(api.client, "GET", f"/api/v1/project-memory/project/{versions.project.id}/items")
    assert [item["id"] for item in active] == [general["id"]]
