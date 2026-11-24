def auth(identity):
    return {"Authorization": "Bearer " + identity["token"]}


def project_for(client, identity):
    response = client.post(
        "/api/v1/projects/",
        json={
            "organization_id": str(identity["organization"].id),
            "workspace_id": str(identity["workspace"].id),
            "name": "Asset Library Project",
        },
        headers=auth(identity),
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_jpeg(client, identity, project_id):
    response = client.post(
        "/api/v1/uploads",
        data={
            "organization_id": str(identity["organization"].id),
            "project_id": str(project_id),
        },
        files={"file": ("inspiration.jpg", b"\xff\xd8\xffasset-content\xff\xd9", "image/jpeg")},
        headers=auth(identity),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_upload_registers_asset_and_supports_metadata_search_move_download_and_restore(
    client, unauthenticated_client, identity_factory, monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "private-assets"))
    identity = identity_factory(role="designer")
    project = project_for(client, identity)
    uploaded = upload_jpeg(client, identity, project["id"])
    asset_id = uploaded["asset_id"]

    details = client.get(f"/api/v1/assets/{asset_id}", headers=auth(identity))
    assert details.status_code == 200
    assert details.json()["display_name"] == "inspiration.jpg"
    assert details.json()["category"] == "image"
    assert details.json()["checksum_sha256"]
    assert "storage_key" not in details.json()
    assert "storage_provider" not in details.json()

    folder = client.post(
        "/api/v1/assets/folders",
        json={
            "organization_id": str(identity["organization"].id),
            "workspace_id": str(identity["workspace"].id),
            "project_id": project["id"],
            "name": "Inspiration",
        },
        headers=auth(identity),
    )
    assert folder.status_code == 201, folder.text
    duplicate = client.post(
        "/api/v1/assets/folders",
        json={
            "organization_id": str(identity["organization"].id),
            "workspace_id": str(identity["workspace"].id),
            "project_id": project["id"],
            "name": "inspiration",
        },
        headers=auth(identity),
    )
    assert duplicate.status_code == 409

    moved = client.post(
        f"/api/v1/assets/{asset_id}/move",
        json={"folder_id": folder.json()["id"]},
        headers=auth(identity),
    )
    assert moved.status_code == 200, moved.text
    renamed = client.patch(
        f"/api/v1/assets/{asset_id}",
        json={"display_name": "Ceremony concept", "category": "inspiration"},
        headers=auth(identity),
    )
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "Ceremony concept"
    protected_update = client.patch(
        f"/api/v1/assets/{asset_id}",
        json={"storage_key": "attacker-controlled"},
        headers=auth(identity),
    )
    assert protected_update.status_code == 422

    listed = client.get(
        "/api/v1/assets",
        params={
            "organization_id": str(identity["organization"].id),
            "project_id": project["id"],
            "q": "ceremony",
            "category": "inspiration",
            "sort_by": "name",
            "limit": 1,
        },
        headers=auth(identity),
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [asset_id]
    unsafe_sort = client.get(
        "/api/v1/assets",
        params={
            "organization_id": str(identity["organization"].id),
            "sort_by": "storage_key",
        },
        headers=auth(identity),
    )
    assert unsafe_sort.status_code == 422
    excessive_page = client.get(
        "/api/v1/assets",
        params={
            "organization_id": str(identity["organization"].id),
            "limit": 101,
        },
        headers=auth(identity),
    )
    assert excessive_page.status_code == 422

    downloaded = client.get(f"/api/v1/assets/{asset_id}/download", headers=auth(identity))
    assert downloaded.status_code == 200
    assert downloaded.content == b"\xff\xd8\xffasset-content\xff\xd9"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert str(tmp_path) not in downloaded.headers.get("content-disposition", "")
    assert unauthenticated_client.get(
        f"/api/v1/assets/{asset_id}/download"
    ).status_code == 401

    deleted = client.delete(f"/api/v1/assets/{asset_id}", headers=auth(identity))
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert client.get(f"/api/v1/assets/{asset_id}/download", headers=auth(identity)).status_code == 404
    assert client.get(
        f"/api/v1/uploads/{uploaded['storage_key']}", headers=auth(identity)
    ).status_code == 404
    restored = client.post(f"/api/v1/assets/{asset_id}/restore", headers=auth(identity))
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert client.get(f"/api/v1/assets/{asset_id}/download", headers=auth(identity)).status_code == 200


def test_asset_and_folder_scope_isolation_and_folder_integrity(
    client, identity_factory, monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "private-assets"))
    owner = identity_factory(role="owner")
    other = identity_factory(role="designer")
    project = project_for(client, owner)
    uploaded = upload_jpeg(client, owner, project["id"])
    asset_id = uploaded["asset_id"]
    outsider = client.get(f"/api/v1/assets/{asset_id}", headers=auth(other))
    assert outsider.status_code == 404

    folder_payload = {
        "organization_id": str(owner["organization"].id),
        "workspace_id": str(owner["workspace"].id),
        "project_id": project["id"],
        "name": "Parent",
    }
    parent = client.post("/api/v1/assets/folders", json=folder_payload, headers=auth(owner))
    assert parent.status_code == 201, parent.text
    child = client.post(
        "/api/v1/assets/folders",
        json={**folder_payload, "name": "Child", "parent_folder_id": parent.json()["id"]},
        headers=auth(owner),
    )
    assert child.status_code == 201, child.text
    cycle = client.patch(
        f"/api/v1/assets/folders/{parent.json()['id']}",
        json={"parent_folder_id": child.json()["id"]},
        headers=auth(owner),
    )
    assert cycle.status_code == 422
    nonempty = client.delete(
        f"/api/v1/assets/folders/{parent.json()['id']}", headers=auth(owner)
    )
    assert nonempty.status_code == 409

    foreign_folder = client.post(
        "/api/v1/assets/folders",
        json={
            "organization_id": str(other["organization"].id),
            "workspace_id": str(other["workspace"].id),
            "name": "Foreign",
        },
        headers=auth(other),
    )
    assert foreign_folder.status_code == 201
    cross_tenant_move = client.post(
        f"/api/v1/assets/{asset_id}/move",
        json={"folder_id": foreign_folder.json()["id"]},
        headers=auth(owner),
    )
    assert cross_tenant_move.status_code == 404
    cross_tenant_folder_access = client.get(
        f"/api/v1/assets/folders/{foreign_folder.json()['id']}", headers=auth(owner)
    )
    assert cross_tenant_folder_access.status_code == 404

    event = client.post(
        "/api/v1/events/",
        json={"project_id": project["id"], "name": "Asset Scope Event"},
        headers=auth(owner),
    ).json()
    same_org_other_workspace = identity_factory(
        role="designer", organization=owner["organization"]
    )
    project_for(client, same_org_other_workspace)
    mismatch = client.get(
        "/api/v1/assets",
        params={
            "organization_id": str(owner["organization"].id),
            "workspace_id": str(same_org_other_workspace["workspace"].id),
            "event_id": event["id"],
        },
        headers=auth(owner),
    )
    assert mismatch.status_code == 404


def test_folder_rename_move_and_empty_soft_delete(client, identity_factory):
    identity = identity_factory(role="designer")
    project = project_for(client, identity)
    base = {
        "organization_id": str(identity["organization"].id),
        "workspace_id": str(identity["workspace"].id),
        "project_id": project["id"],
    }
    first = client.post(
        "/api/v1/assets/folders", json={**base, "name": "First"}, headers=auth(identity)
    ).json()
    second = client.post(
        "/api/v1/assets/folders", json={**base, "name": "Second"}, headers=auth(identity)
    ).json()
    moved = client.patch(
        f"/api/v1/assets/folders/{second['id']}",
        json={"name": "Renamed", "parent_folder_id": first["id"]},
        headers=auth(identity),
    )
    assert moved.status_code == 200
    assert moved.json()["name"] == "Renamed"
    deleted = client.delete(f"/api/v1/assets/folders/{second['id']}", headers=auth(identity))
    assert deleted.status_code == 204
    assert client.get(
        f"/api/v1/assets/folders/{second['id']}", headers=auth(identity)
    ).status_code == 404
    reused_name = client.post(
        "/api/v1/assets/folders",
        json={**base, "name": "Renamed", "parent_folder_id": first["id"]},
        headers=auth(identity),
    )
    assert reused_name.status_code == 201, reused_name.text


def test_upload_registration_failure_compensates_stored_object(
    client, identity_factory, monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    root = tmp_path / "private-assets"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    identity = identity_factory(role="designer")
    project = project_for(client, identity)

    from app.services.asset import AssetService

    def fail_registration(*args, **kwargs):
        raise RuntimeError("simulated database registration failure")

    monkeypatch.setattr(AssetService, "register_uploaded", fail_registration)
    response = client.post(
        "/api/v1/uploads",
        data={
            "organization_id": str(identity["organization"].id),
            "project_id": str(project["id"]),
        },
        files={"file": ("rollback.jpg", b"\xff\xd8\xffrollback\xff\xd9", "image/jpeg")},
        headers=auth(identity),
    )
    assert response.status_code == 503
    assert not [path for path in root.rglob("*") if path.is_file()]
