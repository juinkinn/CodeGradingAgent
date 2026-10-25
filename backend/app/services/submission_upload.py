from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
import stat
from typing import Iterable
import zipfile
import zlib


SUPPORTED_EXTENSIONS = {".cpp", ".c", ".java", ".py"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 5 * 1024 * 1024
MAX_SOURCE_FILES = 100
MAX_ZIP_ENTRIES = 10_000


class UploadValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class SourceFile:
    filename: str
    source_code: str


def _safe_filename(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).name


def _decode_source(filename: str, content: bytes) -> SourceFile:
    if len(content) > MAX_SOURCE_FILE_BYTES:
        raise UploadValidationError(
            f"{filename} exceeds the 5 MiB per-source-file limit.",
            status_code=413,
        )
    try:
        source_code = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadValidationError(
            f"{filename} must contain valid UTF-8 source code."
        ) from exc
    return SourceFile(filename=filename, source_code=source_code)


def collect_source_files(
    uploads: Iterable[tuple[str, bytes]],
) -> list[SourceFile]:
    source_files: list[SourceFile] = []
    total_source_bytes = 0

    def add_source(filename: str, content: bytes) -> None:
        nonlocal total_source_bytes
        safe_name = _safe_filename(filename)
        if (
            not safe_name
            or len(safe_name) > 255
            or any(ord(character) < 32 for character in safe_name)
        ):
            raise UploadValidationError(
                "Uploaded filenames must be 1-255 safe characters."
            )
        if len(source_files) >= MAX_SOURCE_FILES:
            raise UploadValidationError(
                f"An upload batch may contain at most {MAX_SOURCE_FILES} source files.",
                status_code=413,
            )
        if total_source_bytes + len(content) > MAX_UPLOAD_BYTES:
            raise UploadValidationError(
                "Expanded source files exceed the 25 MiB batch limit.",
                status_code=413,
            )
        source_files.append(_decode_source(safe_name, content))
        total_source_bytes += len(content)

    for filename, content in uploads:
        safe_name = _safe_filename(filename)
        if safe_name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    entries = archive.infolist()
                    if len(entries) > MAX_ZIP_ENTRIES:
                        raise UploadValidationError(
                            f"A ZIP archive may contain at most {MAX_ZIP_ENTRIES} entries.",
                            status_code=413,
                        )
                    for entry in entries:
                        if entry.is_dir():
                            continue
                        entry_name = _safe_filename(entry.filename)
                        if (
                            PurePosixPath(entry_name).suffix.lower()
                            not in SUPPORTED_EXTENSIONS
                        ):
                            continue
                        if stat.S_ISLNK(entry.external_attr >> 16):
                            continue
                        if entry.file_size > MAX_SOURCE_FILE_BYTES:
                            raise UploadValidationError(
                                f"{entry_name} exceeds the 5 MiB per-source-file limit.",
                                status_code=413,
                            )
                        if total_source_bytes + entry.file_size > MAX_UPLOAD_BYTES:
                            raise UploadValidationError(
                                "Expanded source files exceed the 25 MiB batch limit.",
                                status_code=413,
                            )
                        with archive.open(entry) as stream:
                            entry_content = stream.read(MAX_SOURCE_FILE_BYTES + 1)
                        add_source(entry_name, entry_content)
            except zipfile.BadZipFile as exc:
                raise UploadValidationError(
                    f"{safe_name} is not a valid ZIP archive."
                ) from exc
            except (
                EOFError,
                NotImplementedError,
                RuntimeError,
                OSError,
                zlib.error,
            ) as exc:
                raise UploadValidationError(
                    f"Could not read ZIP archive {safe_name}."
                ) from exc
            continue

        if PurePosixPath(safe_name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise UploadValidationError(
                f"{safe_name} has an unsupported extension. "
                "Allowed extensions: .cpp, .c, .java, .py."
            )
        add_source(safe_name, content)

    if not source_files:
        raise UploadValidationError(
            "No supported source files were found in the upload."
        )
    return source_files
