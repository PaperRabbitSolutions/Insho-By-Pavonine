from pathlib import Path

from app.api.v1.uploads import _configured_storage
from app.services.storage import LocalStorageProvider, StorageError


ASSET_ROOT = Path(__file__).resolve().parents[2] / "INSHO_Assets"
PREPARED_FILES = [
    (
        ASSET_ROOT / "01_Images/Venues/01_venue_event_visual.jpg",
        "image/jpeg",
    ),
    (
        ASSET_ROOT / "02_Videos/Event_Setups/15496387_1920_1080_50fps.mp4",
        "video/mp4",
    ),
    (
        ASSET_ROOT / "03_Documents/Event_Briefs/01_Event_Brief_From_Video_1.pdf",
        "application/pdf",
    ),
    (
        ASSET_ROOT / "04_Design_Files/Editable_Designs/01_Conference_Stage_Concept.svg",
        "image/svg+xml",
    ),
]


def configure_local_storage(monkeypatch, root, max_bytes="104857600"):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", max_bytes)


def create_project(client, identity):
    response = client.post(
        "/api/v1/projects/",
        json={
            "organization_id": str(identity["organization"].id),
            "workspace_id": str(identity["workspace"].id),
            "name": "Upload Scope Project",
        },
        headers={"Authorization": "Bearer " + identity["token"]},
    )
    assert response.status_code == 201
    return response.json()


def upload(client, identity, path, content_type, *, project_id=None, event_id=None, content=None):
    data = {"organization_id": str(identity["organization"].id)}
    if project_id is not None:
        data["project_id"] = str(project_id)
    if event_id is not None:
        data["event_id"] = str(event_id)
    filename = Path(path).name if path else "test.jpg"
    headers = {"Authorization": "Bearer " + identity["token"]}
    if content is not None:
        files = {"file": (filename, content, content_type)}
        return client.post("/api/v1/uploads", data=data, files=files, headers=headers)
    with open(path, "rb") as source:
        files = {"file": (filename, source, content_type)}
        return client.post("/api/v1/uploads", data=data, files=files, headers=headers)


def test_upload_prepared_assets_and_stream_them_back(client, identity_factory, monkeypatch, tmp_path):
    configure_local_storage(monkeypatch, tmp_path / "private-storage")
    identity = identity_factory(role="designer")
    project = create_project(client, identity)
    for path, content_type in PREPARED_FILES:
        response = upload(
            client, identity, path, content_type, project_id=project["id"]
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert len(body["storage_key"]) == 32
        assert body["original_filename"] == path.name
        assert body["project_id"] == project["id"]
        assert str(tmp_path) not in response.text
        assert "INSHO_Assets" not in response.text

        downloaded = client.get(
            f"/api/v1/uploads/{body['storage_key']}",
            headers={"Authorization": "Bearer " + identity["token"]},
        )
        assert downloaded.status_code == 200
        assert downloaded.content == path.read_bytes()
        assert downloaded.headers["content-disposition"].startswith("attachment;")
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert str(tmp_path) not in downloaded.headers.get("content-disposition", "")
        if path.suffix == ".svg":
            assert downloaded.headers["content-type"].startswith("application/octet-stream")


def test_event_scoped_upload_derives_and_validates_project_scope(
    client, identity_factory, monkeypatch, tmp_path
):
    configure_local_storage(monkeypatch, tmp_path / "private-storage")
    identity = identity_factory(role="designer")
    project = create_project(client, identity)
    event = client.post(
        "/api/v1/events/",
        json={"project_id": project["id"], "name": "Upload Event"},
        headers={"Authorization": "Bearer " + identity["token"]},
    ).json()
    response = upload(
        client, identity, PREPARED_FILES[0][0], "image/jpeg", event_id=event["id"]
    )
    assert response.status_code == 201
    assert response.json()["project_id"] == project["id"]
    assert response.json()["event_id"] == event["id"]

    other_project = create_project(client, identity)
    mismatch = upload(
        client, identity, PREPARED_FILES[0][0], "image/jpeg",
        project_id=other_project["id"], event_id=event["id"],
    )
    assert mismatch.status_code == 404


def test_upload_validation_rejects_empty_large_unsupported_mismatch_and_bad_content(
    client, identity_factory, monkeypatch, tmp_path
):
    root = tmp_path / "private-storage"
    configure_local_storage(monkeypatch, root, max_bytes="64")
    identity = identity_factory(role="designer")
    project = create_project(client, identity)

    empty = upload(
        client, identity, None, "image/jpeg", project_id=project["id"], content=b""
    )
    assert empty.status_code == 422
    too_large = upload(
        client, identity, None, "image/jpeg", project_id=project["id"],
        content=b"\xff\xd8\xff" + b"x" * 80 + b"\xff\xd9",
    )
    assert too_large.status_code == 413
    unsupported = upload(
        client, identity, "sample.bin", "application/octet-stream",
        project_id=project["id"], content=b"x",
    )
    assert unsupported.status_code == 415
    mismatch = upload(
        client, identity, "wrong.pdf", "image/jpeg", project_id=project["id"],
        content=b"%PDF-1.4\n%%EOF",
    )
    assert mismatch.status_code == 415
    malformed = upload(
        client, identity, "fake.jpg", "image/jpeg", project_id=project["id"],
        content=b"not a jpeg",
    )
    assert malformed.status_code == 422
    dangerous_svg = upload(
        client, identity, "unsafe.svg", "image/svg+xml", project_id=project["id"],
        content=b'<!DOCTYPE svg [<!ENTITY x "y">]><svg>&x;</svg>',
    )
    assert dangerous_svg.status_code == 422
    assert not [path for path in root.rglob("*") if path.is_file()]


def test_upload_and_retrieval_require_auth_and_scope(client, unauthenticated_client, identity_factory, monkeypatch, tmp_path):
    configure_local_storage(monkeypatch, tmp_path / "private-storage")
    owner = identity_factory(role="owner")
    member = identity_factory(role="member", organization=owner["organization"])
    foreign = identity_factory(role="designer")
    project = create_project(client, owner)
    foreign_project = create_project(client, foreign)

    unauthenticated = unauthenticated_client.post(
        "/api/v1/uploads",
        data={"organization_id": str(owner["organization"].id)},
        files={"file": ("photo.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")},
    )
    assert unauthenticated.status_code == 401
    forbidden = upload(
        client, member, PREPARED_FILES[0][0], "image/jpeg", project_id=project["id"]
    )
    assert forbidden.status_code == 403
    cross_org_scope = upload(
        client, owner, PREPARED_FILES[0][0], "image/jpeg",
        project_id=foreign_project["id"],
    )
    assert cross_org_scope.status_code == 404

    created = upload(
        client, owner, PREPARED_FILES[0][0], "image/jpeg", project_id=project["id"]
    ).json()
    assert unauthenticated_client.get(
        f"/api/v1/uploads/{created['storage_key']}"
    ).status_code == 401
    hidden = client.get(
        f"/api/v1/uploads/{created['storage_key']}",
        headers={"Authorization": "Bearer " + foreign["token"]},
    )
    assert hidden.status_code == 404
    assert client.get(
        "/api/v1/uploads/not-a-generated-key",
        headers={"Authorization": "Bearer " + owner["token"]},
    ).status_code == 404


def test_storage_failure_removes_partial_object(client, identity_factory, monkeypatch, tmp_path):
    configure_local_storage(monkeypatch, tmp_path / "private-storage")
    identity = identity_factory(role="designer")
    project = create_project(client, identity)

    class FailingProvider:
        def __init__(self):
            self.local = LocalStorageProvider(tmp_path / "failure-storage")
            self.key = None

        def store(self, key, source, metadata):
            self.key = key
            self.local.store(key, source, metadata)
            raise StorageError("simulated failure after write")

        def exists(self, key):
            return self.local.exists(key)

        def delete(self, key):
            return self.local.delete(key)

        def open(self, key):
            return self.local.open(key)

        def get_metadata(self, key):
            return self.local.get_metadata(key)

    failing = FailingProvider()
    client.app.dependency_overrides[_configured_storage] = lambda: failing
    try:
        response = upload(
            client, identity, PREPARED_FILES[0][0], "image/jpeg", project_id=project["id"]
        )
    finally:
        client.app.dependency_overrides.pop(_configured_storage, None)

    assert response.status_code == 503
    assert failing.key is not None
    assert not failing.exists(failing.key)
