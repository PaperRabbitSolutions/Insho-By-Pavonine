import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image, ImageOps
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.asset_derivative import AssetDerivative
from app.services.storage import ObjectNotFound, StorageError, StorageProvider, get_storage_provider


IMAGE_FORMATS = {"image/jpeg": ("JPEG", ".jpg"), "image/png": ("PNG", ".png"), "image/webp": ("WEBP", ".webp")}
IMAGE_MAX_PIXELS_DEFAULT = 24_000_000
IMAGE_MAX_DIMENSION = 16_384
DERIVATIVE_VERSION = "v1"
logger = logging.getLogger(__name__)


class ProcessingFailure(Exception):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _image_max_pixels() -> int:
    try:
        value = int(os.getenv("IMAGE_MAX_PIXELS", str(IMAGE_MAX_PIXELS_DEFAULT)))
    except ValueError as exc:
        raise ProcessingFailure("invalid_processing_config") from exc
    if value <= 0:
        raise ProcessingFailure("invalid_processing_config")
    return min(value, 80_000_000)


def _safe_dimensions(width: int, height: int, limit: int) -> None:
    if width <= 0 or height <= 0 or width > IMAGE_MAX_DIMENSION or height > IMAGE_MAX_DIMENSION:
        raise ProcessingFailure("image_dimensions_exceeded")
    if width * height > limit:
        raise ProcessingFailure("image_pixel_limit_exceeded")


def _rgb_without_metadata(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def _process_image(path: Path, asset: Asset) -> tuple:
    pixel_limit = _image_max_pixels()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as check:
                actual_format = check.format
                original_mode = check.mode
                expected_format = IMAGE_FORMATS.get(asset.mime_type, (None, None))[0]
                if actual_format != expected_format:
                    raise ProcessingFailure("image_format_mismatch")
                raw_width, raw_height = check.size
                _safe_dimensions(raw_width, raw_height, pixel_limit)
                check.verify()
            with Image.open(path) as source:
                orientation = source.getexif().get(274)
                oriented = ImageOps.exif_transpose(source)
                width, height = oriented.size
                _safe_dimensions(width, height, pixel_limit)
                clean = _rgb_without_metadata(oriented)
                derivatives = {}
                for kind, maximum in (("thumbnail", (320, 320)), ("preview", (1600, 1600))):
                    image = clean.copy()
                    image.thumbnail(maximum, Image.Resampling.LANCZOS)
                    output = BytesIO()
                    image.save(output, format="JPEG", quality=85, optimize=False)
                    derivatives[kind] = (output.getvalue(), image.width, image.height)
    except ProcessingFailure:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ProcessingFailure("image_pixel_limit_exceeded") from exc
    except (OSError, ValueError, SyntaxError, RuntimeError) as exc:
        raise ProcessingFailure("malformed_image") from exc

    return {
        "width": width,
        "height": height,
        "format": actual_format,
        "color_mode": original_mode,
        "orientation_applied": orientation not in (None, 1),
        "file_size": asset.file_size,
    }, derivatives


def _video_timeout() -> int:
    try:
        timeout = int(os.getenv("VIDEO_PROCESSING_TIMEOUT_SECONDS", "30"))
    except ValueError as exc:
        raise ProcessingFailure("invalid_processing_config") from exc
    if timeout < 1 or timeout > 120:
        raise ProcessingFailure("invalid_processing_config")
    return timeout


def _processing_source_limit() -> int:
    try:
        value = int(os.getenv("PROCESSING_MAX_SOURCE_BYTES", str(120 * 1024 * 1024)))
    except ValueError as exc:
        raise ProcessingFailure("invalid_processing_config") from exc
    if value <= 0:
        raise ProcessingFailure("invalid_processing_config")
    return value


def _tool_path(env_name: str, default: str) -> str:
    configured = os.getenv(env_name, default)
    path = shutil.which(configured)
    if path is None:
        raise ProcessingFailure("video_tools_unavailable", retryable=True)
    return path


def _run_tool(args, timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            close_fds=True,
            env={"PATH": os.path.dirname(args[0]), "LANG": "C", "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        raise ProcessingFailure("video_processing_timeout", retryable=True) from exc
    except OSError as exc:
        raise ProcessingFailure("video_tool_execution_failed", retryable=True) from exc


def _parse_rate(value: str) -> Optional[float]:
    try:
        numerator, denominator = value.split("/", 1)
        result = float(numerator) / float(denominator)
        return result if math.isfinite(result) and result > 0 else None
    except (ValueError, ZeroDivisionError, AttributeError):
        return None


def _process_video(path: Path, work_dir: Path) -> tuple:
    timeout = _video_timeout()
    ffprobe = _tool_path("FFPROBE_BINARY", "ffprobe")
    ffmpeg = _tool_path("FFMPEG_BINARY", "ffmpeg")
    probe = _run_tool(
        [
            ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
            "-select_streams", "v:0", "-show_entries",
            "format=duration,format_name:stream=codec_name,width,height,avg_frame_rate,r_frame_rate",
            "-of", "json", str(path),
        ],
        timeout,
    )
    if probe.returncode != 0 or len(probe.stdout) > 256 * 1024:
        raise ProcessingFailure("malformed_video")
    try:
        data = json.loads(probe.stdout.decode("utf-8"))
        stream = data["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        duration = float(data.get("format", {}).get("duration", "0"))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("bad duration")
        _safe_dimensions(width, height, 100_000_000)
        frame_rate = _parse_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProcessingFailure("malformed_video") from exc

    timestamp = min(max(duration * 0.1, 0.1), 5.0)
    poster_path = work_dir / "poster.jpg"
    extraction = _run_tool(
        [
            ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-threads", "1", "-ss", f"{timestamp:.3f}", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=640:640:force_original_aspect_ratio=decrease",
            "-q:v", "4", "-y", str(poster_path),
        ],
        timeout,
    )
    if extraction.returncode != 0 or not poster_path.is_file() or poster_path.stat().st_size > 10 * 1024 * 1024:
        raise ProcessingFailure("video_poster_failed", retryable=True)
    try:
        with Image.open(poster_path) as poster:
            poster.load()
            clean = _rgb_without_metadata(poster)
            output = BytesIO()
            clean.save(output, format="JPEG", quality=85, optimize=False)
            poster_bytes = output.getvalue()
            poster_width, poster_height = clean.size
    except (OSError, ValueError) as exc:
        raise ProcessingFailure("video_poster_invalid") from exc
    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "frame_rate": frame_rate,
        "codec": stream.get("codec_name"),
        "container": data.get("format", {}).get("format_name"),
        "file_size": path.stat().st_size,
    }, {"poster": (poster_bytes, poster_width, poster_height)}


class AssetProcessingService:
    @staticmethod
    def _store_derivative(
        db: Session, asset: Asset, provider: StorageProvider, kind: str,
        content: bytes, width: Optional[int], height: Optional[int],
    ) -> AssetDerivative:
        key = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"insho:asset:{asset.id}:{kind}:{DERIVATIVE_VERSION}",
        ).hex
        existing = db.scalars(
            select(AssetDerivative).where(
                AssetDerivative.asset_id == asset.id,
                AssetDerivative.kind == kind,
            )
        ).first()
        conflicting_asset = db.scalars(
            select(Asset.id).where(Asset.storage_key == key).limit(1)
        ).first()
        if conflicting_asset is not None:
            raise ProcessingFailure("derivative_key_conflict")
        if existing is not None and provider.exists(existing.storage_key):
            return existing
        if existing is not None:
            try:
                provider.delete(existing.storage_key)
            except ObjectNotFound:
                pass
            db.delete(existing)
            db.commit()
        if provider.exists(key):
            try:
                provider.delete(key)
            except ObjectNotFound:
                pass
        checksum = hashlib.sha256(content).hexdigest()
        metadata = {
            "asset_id": str(asset.id),
            "kind": kind,
            "mime_type": "image/jpeg",
            "size": len(content),
            "checksum_sha256": checksum,
            "width": width,
            "height": height,
        }
        stored = False
        try:
            provider.store(key, BytesIO(content), metadata)
            stored = True
            derivative = AssetDerivative(
                asset_id=asset.id,
                kind=kind,
                storage_key=key,
                storage_provider="local",
                mime_type="image/jpeg",
                file_extension=".jpg",
                file_size=len(content),
                checksum_sha256=checksum,
                width=width,
                height=height,
            )
            db.add(derivative)
            db.commit()
            db.refresh(derivative)
            return derivative
        except Exception:
            logger.exception("Asset media processing failed for asset %s", asset_id)
            db.rollback()
            if stored:
                try:
                    provider.delete(key)
                except Exception:
                    pass
            raise

    @classmethod
    def process_asset(cls, db: Session, asset_id: uuid.UUID, provider: Optional[StorageProvider] = None) -> Dict[str, Any]:
        provider = provider or get_storage_provider()
        asset = db.scalars(select(Asset).where(Asset.id == asset_id).with_for_update()).first()
        if asset is None or asset.deleted_at is not None or asset.status != "active":
            return {"status": "skipped", "reason": "asset_unavailable"}
        if asset.processing_status == "not_applicable":
            return {"status": asset.processing_status}
        if asset.processing_status == "complete":
            expected_kinds = {"thumbnail", "preview"} if asset.mime_type in IMAGE_FORMATS else {"poster"}
            derivative_rows = db.scalars(
                select(AssetDerivative).where(
                    AssetDerivative.asset_id == asset.id,
                    AssetDerivative.kind.in_(expected_kinds),
                )
            ).all()
            if {row.kind for row in derivative_rows} == expected_kinds and all(
                provider.exists(row.storage_key) for row in derivative_rows
            ):
                return {"status": "complete"}
        now = datetime.now(timezone.utc)
        if asset.processing_status == "processing" and asset.processing_started_at:
            if asset.processing_started_at > now - timedelta(minutes=10):
                return {"status": "skipped", "reason": "already_processing"}
        asset.processing_status = "processing"
        asset.processing_attempts += 1
        asset.processing_started_at = now
        asset.processing_error_code = None
        db.commit()

        try:
            with tempfile.TemporaryDirectory(prefix="insho-media-") as temp_dir:
                work_dir = Path(temp_dir)
                input_path = work_dir / ("original" + asset.file_extension)
                max_source_bytes = _processing_source_limit()
                try:
                    source = provider.open(asset.storage_key)
                    with source, input_path.open("wb") as destination:
                        copied = 0
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            copied += len(chunk)
                            if copied > asset.file_size:
                                raise ProcessingFailure("source_size_mismatch")
                            if copied > max_source_bytes:
                                raise ProcessingFailure("processing_source_limit_exceeded")
                            destination.write(chunk)
                except ObjectNotFound as exc:
                    raise ProcessingFailure("original_missing") from exc
                except StorageError as exc:
                    raise ProcessingFailure("storage_unavailable", retryable=True) from exc
                if copied != asset.file_size:
                    raise ProcessingFailure("source_size_mismatch")

                if asset.mime_type in IMAGE_FORMATS:
                    metadata, derivatives = _process_image(input_path, asset)
                elif asset.mime_type == "video/mp4":
                    metadata, derivatives = _process_video(input_path, work_dir)
                else:
                    asset.processing_status = "not_applicable"
                    asset.processing_completed_at = datetime.now(timezone.utc)
                    asset.media_metadata = {"file_size": asset.file_size, "format": asset.mime_type}
                    db.commit()
                    return {"status": "not_applicable"}

                for kind, (content, width, height) in derivatives.items():
                    cls._store_derivative(db, asset, provider, kind, content, width, height)
                asset.media_metadata = metadata
                asset.processing_status = "complete"
                asset.processing_error_code = None
                asset.processing_completed_at = datetime.now(timezone.utc)
                db.commit()
                return {"status": "complete", "derivatives": list(derivatives)}
        except ProcessingFailure as exc:
            db.rollback()
            failed = db.get(Asset, asset_id)
            if failed is not None:
                failed.processing_status = "failed"
                failed.processing_error_code = exc.code
                failed.processing_completed_at = datetime.now(timezone.utc)
                db.commit()
            return {"status": "failed", "error_code": exc.code, "retryable": exc.retryable}
        except Exception:
            db.rollback()
            failed = db.get(Asset, asset_id)
            if failed is not None:
                failed.processing_status = "failed"
                failed.processing_error_code = "processing_failed"
                failed.processing_completed_at = datetime.now(timezone.utc)
                db.commit()
            return {"status": "failed", "error_code": "processing_failed", "retryable": True}


def retry_asset_processing(asset_id: uuid.UUID) -> Dict[str, Any]:
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        return AssetProcessingService.process_asset(db, uuid.UUID(str(asset_id)))
    finally:
        db.close()
