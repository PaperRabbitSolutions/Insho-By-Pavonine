import io
import json
from types import SimpleNamespace
from uuid import UUID

from PIL import Image
from sqlalchemy import select

from app.models.asset import Asset
from app.models.asset_derivative import AssetDerivative
from app.services.asset_lifecycle import cleanup_deleted_assets
from app.services.media_processing import AssetProcessingService
from app.services.storage import LocalStorageProvider


def headers(identity):
    return {"Authorization": "Bearer " + identity["token"]}


def new_image_bytes(fmt="PNG", *, size=(64, 32), exif=None):
    output = io.BytesIO()
    image = Image.new("RGB", size, color=(120, 45, 210))
    exif_value = exif.tobytes() if hasattr(exif, "tobytes") else (exif or b"")
    image.save(output, format=fmt, exif=exif_value)
    return output.getvalue()


def create_project(client, identity):
    response = client.post(
        "/api/v1/projects/",
        json={
            "organization_id": str(identity["organization"].id),
            "workspace_id": str(identity["workspace"].id),
            "name": "Media Processing Project",
        },
        headers=headers(identity),
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_image(client, identity, project, content, filename="image.png"):
    extension = filename.rsplit(".", 1)[-1].lower()
    mime_type = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[extension]
    response = client.post(
        "/api/v1/uploads",
        data={
            "organization_id": str(identity["organization"].id),
            "project_id": project["id"],
        },
        files={"file": (filename, content, mime_type)},
        headers=headers(identity),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_image_processing_creates_private_derivatives_and_strips_exif(
    client, db_session, identity_factory, monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "media-storage"))
    identity = identity_factory(role="designer")
    project = create_project(client, identity)
    exif = Image.Exif()
    exif[274] = 6
    exif[271] = "Private Camera Model"
    original_bytes = new_image_bytes("JPEG", size=(48, 24), exif=exif)
    uploaded = upload_image(client, identity, project, original_bytes, "orientation.jpg")
    asset_id = UUID(uploaded["asset_id"])
    assert uploaded["category"] == "image"

    provider = LocalStorageProvider(tmp_path / "media-storage")
    original_before = provider.open(uploaded["storage_key"]).read()
    assert original_before == original_bytes
    result = AssetProcessingService.process_asset(db_session, asset_id, provider)
    assert result["status"] == "complete"
    asset = db_session.get(Asset, asset_id)
    assert asset.processing_attempts == 1
    assert asset.processing_status == "complete"
    assert asset.media_metadata["format"] == "JPEG"
    assert (asset.media_metadata["width"], asset.media_metadata["height"]) == (24, 48)
    assert asset.media_metadata["orientation_applied"] is True
    assert "exif" not in str(asset.media_metadata).lower()

    derivatives = db_session.scalars(
        select(AssetDerivative).where(AssetDerivative.asset_id == asset_id)
    ).all()
    assert {item.kind for item in derivatives} == {"thumbnail", "preview"}
    for derivative in derivatives:
        with provider.open(derivative.storage_key) as preview_file:
            preview_bytes = preview_file.read()
        with Image.open(io.BytesIO(preview_bytes)) as preview:
            assert preview.format == "JPEG"
            assert not preview.getexif()
    assert provider.open(uploaded["storage_key"]).read() == original_bytes

    repeated = AssetProcessingService.process_asset(db_session, asset_id, provider)
    assert repeated["status"] == "complete"
    assert db_session.query(AssetDerivative).filter_by(asset_id=asset_id).count() == 2
    preview_response = client.get(
        f"/api/v1/assets/{asset_id}/preview/thumbnail", headers=headers(identity)
    )
    assert preview_response.status_code == 200
    assert preview_response.headers["content-type"] == "image/jpeg"
    assert preview_response.headers["x-content-type-options"] == "nosniff"
    assert preview_response.headers["content-disposition"] == "inline"
    outsider = identity_factory(role="designer")
    denied_preview = client.get(
        f"/api/v1/assets/{asset_id}/preview/thumbnail", headers=headers(outsider)
    )
    assert denied_preview.status_code == 404


def test_png_webp_processing_and_decompression_limit(
    client, db_session, identity_factory, monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "images"))
    identity = identity_factory(role="designer")
    project = create_project(client, identity)
    provider = LocalStorageProvider(tmp_path / "images")

    formats = [("PNG", "image.png", "image/png")]
    if Image.registered_extensions().get(".webp") == "WEBP":
        formats.append(("WEBP", "image.webp", "image/webp"))
    for fmt, filename, mime in formats:
        content = new_image_bytes(fmt)
        uploaded = client.post(
            "/api/v1/uploads",
            data={
                "organization_id": str(identity["organization"].id),
                "project_id": project["id"],
            },
            files={"file": (filename, content, mime)},
            headers=headers(identity),
        )
        assert uploaded.status_code == 201, uploaded.text
        processed = AssetProcessingService.process_asset(
            db_session, UUID(uploaded.json()["asset_id"]), provider
        )
        assert processed["status"] == "complete", processed

    monkeypatch.setenv("IMAGE_MAX_PIXELS", "1000")
    too_many_pixels = upload_image(
        client, identity, project, new_image_bytes(size=(40, 40)), "large.png"
    )
    failed = AssetProcessingService.process_asset(
        db_session, UUID(too_many_pixels["asset_id"]), provider
    )
    assert failed["status"] == "failed"
    assert failed["error_code"] == "image_pixel_limit_exceeded"
    assert provider.exists(too_many_pixels["storage_key"])


def test_video_tool_failure_preserves_original_and_can_be_retried(
    client, db_session, identity_factory, monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    root = tmp_path / "video-storage"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    monkeypatch.setenv("FFPROBE_BINARY", "missing-ffprobe-for-test")
    identity = identity_factory(role="designer")
    project = create_project(client, identity)
    # The upload layer checks the MP4 ftyp box; decoding is handled by configured ffprobe/ffmpeg.
    mp4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
    uploaded = client.post(
        "/api/v1/uploads",
        data={
            "organization_id": str(identity["organization"].id),
            "project_id": project["id"],
        },
        files={"file": ("clip.mp4", mp4, "video/mp4")},
        headers=headers(identity),
    )
    assert uploaded.status_code == 201, uploaded.text
    asset_id = UUID(uploaded.json()["asset_id"])
    provider = LocalStorageProvider(root)
    result = AssetProcessingService.process_asset(db_session, asset_id, provider)
    assert result["status"] == "failed"
    assert result["error_code"] == "video_tools_unavailable"
    assert provider.open(uploaded.json()["storage_key"]).read() == mp4
    retry = client.post(
        f"/api/v1/assets/{asset_id}/processing/retry", headers=headers(identity)
    )
    assert retry.status_code == 200
    assert retry.json()["processing_status"] == "pending"


def test_queue_outage_marks_processing_failed_without_failing_upload(
    client, db_session, identity_factory, monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "queue-storage"))
    identity = identity_factory(role="designer")
    project = create_project(client, identity)
    uploaded = upload_image(client, identity, project, new_image_bytes())
    asset_id = UUID(uploaded["asset_id"])

    import app.services.processing_queue as queue
    from app.workers.tasks import process_asset

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("private broker details")

    monkeypatch.setattr(process_asset, "apply_async", unavailable)
    monkeypatch.setattr(queue, "SessionLocal", lambda: db_session)
    assert queue.enqueue_asset_processing(asset_id) is False
    db_session.refresh(db_session.get(Asset, asset_id))
    asset = db_session.get(Asset, asset_id)
    assert asset.processing_status == "failed"
    assert asset.processing_error_code == "queue_unavailable"
    assert client.get(
        f"/api/v1/assets/{asset_id}/download", headers=headers(identity)
    ).status_code == 200


def test_video_metadata_and_poster_pipeline_with_stubbed_external_tools(
    client, db_session, identity_factory, monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    root = tmp_path / "video-stub-storage"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    identity = identity_factory(role="designer")
    project = create_project(client, identity)
    mp4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
    uploaded = client.post(
        "/api/v1/uploads",
        data={
            "organization_id": str(identity["organization"].id),
            "project_id": project["id"],
        },
        files={"file": ("clip.mp4", mp4, "video/mp4")},
        headers=headers(identity),
    )
    assert uploaded.status_code == 201, uploaded.text

    import app.services.media_processing as processing

    monkeypatch.setattr(processing, "_tool_path", lambda _env, default: "/mock/" + default)

    def fake_run(args, _timeout):
        if args[0].endswith("ffprobe"):
            payload = {
                "format": {"duration": "12.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                "streams": [{
                    "codec_name": "h264", "width": 1920, "height": 1080,
                    "avg_frame_rate": "30000/1001", "r_frame_rate": "30/1",
                }],
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload).encode())
        poster_path = args[-1]
        Image.new("RGB", (320, 180), color=(20, 90, 150)).save(poster_path, format="JPEG")
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(processing, "_run_tool", fake_run)
    provider = LocalStorageProvider(root)
    result = AssetProcessingService.process_asset(
        db_session, UUID(uploaded.json()["asset_id"]), provider
    )
    assert result["status"] == "complete", result
    asset = db_session.get(Asset, UUID(uploaded.json()["asset_id"]))
    assert asset.media_metadata["duration_seconds"] == 12.5
    assert asset.media_metadata["codec"] == "h264"
    assert asset.media_metadata["frame_rate"] == 30000 / 1001
    poster = db_session.scalars(
        select(AssetDerivative).where(
            AssetDerivative.asset_id == asset.id,
            AssetDerivative.kind == "poster",
        )
    ).one()
    with provider.open(poster.storage_key) as stream:
        with Image.open(stream) as image:
            assert image.format == "JPEG"
            assert image.size == (320, 180)


def test_storage_usage_deduplication_and_manual_cleanup(
    client, db_session, identity_factory, monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    root = tmp_path / "accounting-storage"
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(root))
    identity = identity_factory(role="designer")
    project = create_project(client, identity)
    content = new_image_bytes()
    first = upload_image(client, identity, project, content, "same.png")
    second = upload_image(client, identity, project, content, "different-name.png")
    assert first["duplicate_of_asset_id"] is None
    assert second["duplicate_of_asset_id"] == first["asset_id"]
    assert first["storage_key"] != second["storage_key"]
    same_name_different_content = upload_image(
        client, identity, project, new_image_bytes(size=(65, 32)), "same.png"
    )
    assert same_name_different_content["duplicate_of_asset_id"] is None
    other_tenant = identity_factory(role="designer")
    other_project = create_project(client, other_tenant)
    cross_tenant = upload_image(client, other_tenant, other_project, content, "same.png")
    assert cross_tenant["duplicate_of_asset_id"] is None

    provider = LocalStorageProvider(root)
    processed = AssetProcessingService.process_asset(db_session, UUID(first["asset_id"]), provider)
    assert processed["status"] == "complete", processed
    usage = client.get(
        "/api/v1/assets/usage",
        params={"organization_id": str(identity["organization"].id), "project_id": project["id"]},
        headers=headers(identity),
    )
    assert usage.status_code == 200
    assert usage.json()["asset_count"] == 3
    assert usage.json()["original_bytes"] == 2 * len(content) + len(new_image_bytes(size=(65, 32)))
    assert usage.json()["derivative_bytes"] > 0
    assert usage.json()["total_bytes"] == usage.json()["original_bytes"] + usage.json()["derivative_bytes"]

    asset = db_session.get(Asset, UUID(first["asset_id"]))
    asset.status = "deleted"
    from datetime import datetime, timedelta, timezone

    asset.deleted_at = datetime.now(timezone.utc) - timedelta(days=31)
    db_session.commit()
    dry_run = cleanup_deleted_assets(
        db=db_session, provider=provider, retention_days=30,
        confirm_permanent_delete=False,
    )
    assert dry_run["dry_run"] is True and dry_run["eligible"] == 1
    assert provider.exists(first["storage_key"])
    removed = cleanup_deleted_assets(
        db=db_session, provider=provider, retention_days=30,
        confirm_permanent_delete=True,
    )
    assert removed["removed"] == 1
    assert db_session.get(Asset, UUID(first["asset_id"])) is None
    assert not provider.exists(first["storage_key"])
    assert provider.exists(second["storage_key"])
    assert client.get(
        f"/api/v1/assets/{second['asset_id']}/download", headers=headers(identity)
    ).status_code == 200
    assert all(not provider.exists(item.storage_key) for item in db_session.scalars(
        select(AssetDerivative).where(AssetDerivative.asset_id == UUID(first["asset_id"]))
    ).all())
