
import uuid


def unique_name(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_create_event_type(client):
    name = unique_name("Wedding")

    response = client.post(
        "/api/v1/event-types/",
        json={
            "name": name,
            "description": "Wedding events and celebrations"
        }
    )

    assert response.status_code == 201
    assert response.json()["name"] == name
    assert response.json()["description"] == "Wedding events and celebrations"
    assert "id" in response.json()


def test_get_all_event_types(client):
    name = unique_name("Corporate")

    client.post(
        "/api/v1/event-types/",
        json={"name": name}
    )

    response = client.get("/api/v1/event-types/")

    assert response.status_code == 200
    assert any(item["name"] == name for item in response.json())


def test_get_event_type_by_id(client):
    name = unique_name("Birthday")

    create_response = client.post(
        "/api/v1/event-types/",
        json={"name": name}
    )

    event_type_id = create_response.json()["id"]

    response = client.get(
        f"/api/v1/event-types/{event_type_id}"
    )

    assert response.status_code == 200
    assert response.json()["id"] == event_type_id


def test_get_nonexistent_event_type(client):
    event_type_id = uuid.uuid4()

    response = client.get(
        f"/api/v1/event-types/{event_type_id}"
    )

    assert response.status_code == 404


def test_update_event_type(client):
    create_response = client.post(
        "/api/v1/event-types/",
        json={"name": unique_name("Original")}
    )

    event_type_id = create_response.json()["id"]
    updated_name = unique_name("Updated")

    response = client.patch(
        f"/api/v1/event-types/{event_type_id}",
        json={"name": updated_name}
    )

    assert response.status_code == 200
    assert response.json()["name"] == updated_name


def test_delete_event_type(client):
    create_response = client.post(
        "/api/v1/event-types/",
        json={"name": unique_name("Delete")}
    )

    event_type_id = create_response.json()["id"]

    response = client.delete(
        f"/api/v1/event-types/{event_type_id}"
    )

    assert response.status_code == 204


def test_delete_nonexistent_event_type(client):
    event_type_id = uuid.uuid4()

    response = client.delete(
        f"/api/v1/event-types/{event_type_id}"
    )

    assert response.status_code == 404
