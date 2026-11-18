"""Cryptographic hashing, slugification, and file integrity helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def compute_sha256(path: Path) -> str:
    """Return hex SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_md5(path: Path) -> str:
    """Return hex MD5 digest (matching Google Drive md5Checksum field)."""
    digest = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify_name(name: str) -> str:
    """Convert an asset name into a safe filesystem/Drive slug."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return cleaned[:48] or "asset"
