import json
import os
import re
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Protocol

from app.config import StorageSettings, get_storage_settings


_STORAGE_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_CHUNK_SIZE = 1024 * 1024


class StorageError(Exception):
    """A safe, provider-neutral storage failure."""


class ObjectAlreadyExists(StorageError):
    pass


class ObjectNotFound(StorageError):
    pass


class InvalidStorageKey(ValueError):
    pass


class StorageProvider(Protocol):
    def store(self, key: str, source: BinaryIO, metadata: Dict[str, Any]) -> None:
        ...

    def open(self, key: str) -> BinaryIO:
        ...

    def exists(self, key: str) -> bool:
        ...

    def get_metadata(self, key: str) -> Dict[str, Any]:
        ...

    def delete(self, key: str) -> None:
        ...


def validate_storage_key(key: str) -> str:
    if not isinstance(key, str) or not _STORAGE_KEY_RE.fullmatch(key):
        raise InvalidStorageKey("Invalid storage reference.")
    return key


class LocalStorageProvider:
    """Private filesystem storage; returned keys are opaque UUID hex tokens."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.root, 0o700)
        except OSError as exc:
            raise StorageError("Local storage is unavailable.") from exc

    def _paths(self, key: str, *, create_parent: bool = False):
        key = validate_storage_key(key)
        parent = self.root
        for segment in (key[:2], key[2:4]):
            parent = parent / segment
            if parent.is_symlink():
                raise InvalidStorageKey("Invalid storage reference.")
            if create_parent:
                parent.mkdir(exist_ok=True, mode=0o700)
                os.chmod(parent, 0o700)
        resolved_parent = parent.resolve()
        try:
            if os.path.commonpath((str(self.root), str(resolved_parent))) != str(self.root):
                raise InvalidStorageKey("Invalid storage reference.")
        except ValueError as exc:
            raise InvalidStorageKey("Invalid storage reference.") from exc
        return resolved_parent / key, resolved_parent / (key + ".json")

    @staticmethod
    def _open_flags(flags: int) -> int:
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return flags

    def store(self, key: str, source: BinaryIO, metadata: Dict[str, Any]) -> None:
        data_path, metadata_path = self._paths(key, create_parent=True)
        try:
            metadata_json = json.dumps(metadata, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise StorageError("Storage metadata could not be recorded.") from exc

        if os.path.lexists(data_path) or os.path.lexists(metadata_path):
            raise ObjectAlreadyExists("Storage reference already exists.")

        data_fd = None
        data_created = False
        metadata_created = False
        try:
            data_fd = os.open(
                str(data_path),
                self._open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                0o600,
            )
            data_created = True
            if hasattr(source, "seek"):
                source.seek(0)
            with os.fdopen(data_fd, "wb") as output:
                data_fd = None
                while True:
                    chunk = source.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())

            metadata_fd = os.open(
                str(metadata_path),
                self._open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
                0o600,
            )
            metadata_created = True
            with os.fdopen(metadata_fd, "w", encoding="utf-8") as output:
                output.write(metadata_json)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError as exc:
            self._cleanup(
                data_path if data_created else None,
                metadata_path if metadata_created else None,
            )
            raise ObjectAlreadyExists("Storage reference already exists.") from exc
        except Exception as exc:
            self._cleanup(
                data_path if data_created else None,
                metadata_path if metadata_created else None,
            )
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise StorageError("Failed to store file.") from exc
        finally:
            if data_fd is not None:
                os.close(data_fd)

    @staticmethod
    def _cleanup(*paths: Optional[Path]) -> None:
        for path in paths:
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def open(self, key: str) -> BinaryIO:
        data_path, _ = self._paths(key)
        try:
            fd = os.open(str(data_path), self._open_flags(os.O_RDONLY))
            return os.fdopen(fd, "rb")
        except FileNotFoundError as exc:
            raise ObjectNotFound("Stored file not found.") from exc
        except OSError as exc:
            if isinstance(exc, PermissionError):
                raise StorageError("Failed to open stored file.") from exc
            raise ObjectNotFound("Stored file not found.") from exc

    def exists(self, key: str) -> bool:
        data_path, metadata_path = self._paths(key)
        return (
            data_path.is_file()
            and not data_path.is_symlink()
            and metadata_path.is_file()
            and not metadata_path.is_symlink()
        )

    def get_metadata(self, key: str) -> Dict[str, Any]:
        _, metadata_path = self._paths(key)
        try:
            fd = os.open(str(metadata_path), self._open_flags(os.O_RDONLY))
            with os.fdopen(fd, "r", encoding="utf-8") as source:
                metadata = json.load(source)
        except FileNotFoundError as exc:
            raise ObjectNotFound("Stored file not found.") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StorageError("Failed to read stored file metadata.") from exc
        if not isinstance(metadata, dict):
            raise StorageError("Stored file metadata is invalid.")
        return metadata

    def delete(self, key: str) -> None:
        data_path, metadata_path = self._paths(key)
        found = False
        for path in (data_path, metadata_path):
            try:
                path.unlink()
                found = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise StorageError("Failed to delete stored file.") from exc
        if not found:
            raise ObjectNotFound("Stored file not found.")


def get_storage_provider(settings: Optional[StorageSettings] = None) -> StorageProvider:
    settings = settings or get_storage_settings()
    if settings.provider == "local":
        return LocalStorageProvider(settings.local_root)
    raise RuntimeError("Configured storage provider is not available.")
