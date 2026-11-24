import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import BinaryIO, Dict

from app.config import StorageSettings


SUPPORTED_FORMATS: Dict[str, str] = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".svg": "image/svg+xml",
}


class UploadValidationError(ValueError):
    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    extension: str
    content_type: str
    size: int


def validate_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename:
        raise UploadValidationError("A filename is required.")
    if len(filename.encode("utf-8", errors="replace")) > 255:
        raise UploadValidationError("Filename is too long.")
    if filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise UploadValidationError("Filename must be a safe base filename.")
    if any(ord(char) < 32 or ord(char) == 127 for char in filename):
        raise UploadValidationError("Filename contains unsupported characters.")
    if not filename.strip():
        raise UploadValidationError("A filename is required.")
    return filename


def validate_declared_type(
    filename: str,
    declared_content_type: str,
    settings: StorageSettings,
) -> ValidatedUpload:
    filename = validate_filename(filename)
    extension = filename.rsplit(".", 1)[-1].lower()
    extension = "." + extension if "." in filename else ""
    if extension not in settings.allowed_extensions or extension not in SUPPORTED_FORMATS:
        raise UploadValidationError("File extension is not allowed.", 415)

    expected_type = SUPPORTED_FORMATS[extension]
    declared_type = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if (
        expected_type not in settings.allowed_mime_types
        or declared_type not in settings.allowed_mime_types
        or declared_type != expected_type
    ):
        raise UploadValidationError("File content type does not match the allowlist.", 415)

    return ValidatedUpload(filename, extension, expected_type, 0)


def _validate_svg(source: BinaryIO) -> None:
    source.seek(0)
    overlap = b""
    prefix = b""
    while True:
        chunk = source.read(64 * 1024)
        if not chunk:
            break
        if len(prefix) < 8192:
            prefix += chunk[: 8192 - len(prefix)]
        search = (overlap + chunk).upper()
        if b"<!DOCTYPE" in search or b"<!ENTITY" in search:
            raise UploadValidationError("SVG declarations with entities are not allowed.")
        overlap = search[-16:]

    if b"\x00" in prefix:
        raise UploadValidationError("SVG must use UTF-8 text encoding.")

    source.seek(0)
    first_element = True
    try:
        for event, element in ElementTree.iterparse(source, events=("start", "end")):
            if event == "start" and first_element:
                first_element = False
                tag = element.tag.rsplit("}", 1)[-1].lower()
                if tag != "svg":
                    raise UploadValidationError("File content does not match SVG format.")
            if event == "end":
                element.clear()
    except ElementTree.ParseError as exc:
        raise UploadValidationError("SVG file is malformed.") from exc
    if first_element:
        raise UploadValidationError("SVG file is empty or malformed.")


def validate_file_content(
    source: BinaryIO,
    extension: str,
    size: int,
) -> None:
    if size <= 0:
        raise UploadValidationError("Empty files cannot be uploaded.")
    source.seek(0)
    head = source.read(32)

    if extension == ".jpg":
        source.seek(max(0, size - 2))
        if not head.startswith(b"\xff\xd8\xff") or source.read(2) != b"\xff\xd9":
            raise UploadValidationError("File content does not match JPEG format.")
    elif extension == ".png":
        if not head.startswith(b"\x89PNG\r\n\x1a\n"):
            raise UploadValidationError("File content does not match PNG format.")
    elif extension == ".webp":
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WEBP":
            raise UploadValidationError("File content does not match WebP format.")
    elif extension == ".pdf":
        source.seek(max(0, size - 1024))
        if not head.startswith(b"%PDF-") or b"%%EOF" not in source.read(1024):
            raise UploadValidationError("File content does not match PDF format.")
    elif extension == ".mp4":
        if len(head) < 12 or head[4:8] != b"ftyp":
            raise UploadValidationError("File content does not match MP4 format.")
    elif extension == ".svg":
        _validate_svg(source)
    else:
        raise UploadValidationError("File format is not supported.", 415)
    source.seek(0)
