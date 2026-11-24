import os
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet


def _csv_setting(name: str, default: str, *, lower: bool = True) -> FrozenSet[str]:
    raw = os.getenv(name, default)
    parts = (part.strip() for part in raw.split(","))
    return frozenset(part.lower() if lower else part for part in parts if part.strip())


@dataclass(frozen=True)
class StorageSettings:
    provider: str
    local_root: Path
    max_upload_bytes: int
    allowed_extensions: FrozenSet[str]
    allowed_mime_types: FrozenSet[str]


def get_storage_settings() -> StorageSettings:
    provider = os.getenv("STORAGE_PROVIDER", "local").strip().lower()
    if provider != "local":
        raise RuntimeError("Only the local storage provider is configured in this batch.")

    try:
        max_upload_bytes = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(100 * 1024 * 1024)))
    except ValueError as exc:
        raise RuntimeError("MAX_UPLOAD_SIZE_BYTES must be a positive integer.") from exc
    if max_upload_bytes <= 0:
        raise RuntimeError("MAX_UPLOAD_SIZE_BYTES must be a positive integer.")

    extensions = _csv_setting(
        "UPLOAD_ALLOWED_EXTENSIONS", ".jpg,.png,.webp,.mp4,.pdf,.svg"
    )
    extensions = frozenset(
        extension if extension.startswith(".") else "." + extension
        for extension in extensions
    )
    mime_types = _csv_setting(
        "UPLOAD_ALLOWED_MIME_TYPES",
        "image/jpeg,image/png,image/webp,video/mp4,application/pdf,image/svg+xml",
    )
    if not extensions or not mime_types:
        raise RuntimeError("Upload extension and MIME allowlists cannot be empty.")

    return StorageSettings(
        provider=provider,
        local_root=Path(os.getenv("LOCAL_STORAGE_ROOT", "./storage")).expanduser().resolve(),
        max_upload_bytes=max_upload_bytes,
        allowed_extensions=extensions,
        allowed_mime_types=mime_types,
    )
