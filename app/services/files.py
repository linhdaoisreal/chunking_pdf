"""Persist sources on the shared volume; never derive paths from client filenames."""

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.errors import AppError


@dataclass(frozen=True)
class SavedFile:
    key: str
    content_hash: str


def source_path(root: Path, key: str) -> Path:
    path = (root / key).resolve()
    if path.parent != root.resolve() or path.suffix != ".pdf":
        raise ValueError("Invalid source key")
    return path


def remove_source(root: Path, key: str) -> None:
    source_path(root, key).unlink(missing_ok=True)


def source_available(root: Path, key: str | None, expires_at: datetime | None) -> bool:
    return bool(
        key
        and (expires_at is None or expires_at > datetime.now(UTC))
        and source_path(root, key).is_file()
    )


def save_pdf(stream: BinaryIO, root: Path, max_bytes: int) -> SavedFile:
    root.mkdir(parents=True, exist_ok=True)
    key = f"{uuid4().hex}.pdf"
    path = source_path(root, key)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("xb") as target:
            while chunk := stream.read(64 * 1024):
                if size == 0 and not chunk.startswith(b"%PDF-"):
                    raise AppError(415, "invalid_pdf", "File must have a PDF signature")
                size += len(chunk)
                if size > max_bytes:
                    raise AppError(413, "payload_too_large", "PDF exceeds upload limit")
                target.write(chunk)
                digest.update(chunk)
            if not size:
                raise AppError(415, "invalid_pdf", "PDF is empty")
            target.flush()
            os.fsync(target.fileno())
        if os.name != "nt":
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return SavedFile(key, digest.hexdigest())
