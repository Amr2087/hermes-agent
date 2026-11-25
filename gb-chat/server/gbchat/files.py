"""The shared workspace: files the assistant makes (outputs/) and files people upload (uploads/)."""

from __future__ import annotations

import mimetypes
import re
import time
from pathlib import Path
from typing import Any

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,150}$")

KINDS = {
    ".docx": "Word", ".doc": "Word", ".pptx": "PowerPoint", ".xlsx": "Excel", ".xls": "Excel",
    ".csv": "CSV", ".pdf": "PDF", ".html": "Web page", ".htm": "Web page", ".md": "Text",
    ".txt": "Text", ".png": "Image", ".jpg": "Image", ".jpeg": "Image", ".svg": "Image", ".json": "Data",
    ".zip": "Archive",
}

mimetypes.add_type("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx")
mimetypes.add_type("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx")
mimetypes.add_type("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx")


def clean_name(name: str) -> str | None:
    """A plain file name (no folders), or None when it can't be made safe."""
    name = Path(name.replace("\\", "/")).name.strip()
    name = re.sub(r"\s+", " ", name)
    if not name or name.startswith(".") or not _SAFE_NAME.match(name):
        cleaned = re.sub(r"[^A-Za-z0-9._()\- ]", "_", name).lstrip("._ ")[:150]
        if not cleaned or not _SAFE_NAME.match(cleaned):
            return None
        name = cleaned
    return name


def resolve_in(folder: Path, name: str) -> Path | None:
    """The path of ``name`` directly inside ``folder``, or None if it would escape it."""
    if clean_name(name) != name:
        return None
    path = (folder / name).resolve()
    try:
        path.relative_to(folder.resolve())
    except ValueError:
        return None
    return path


def describe(path: Path, url_prefix: str) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "url": f"{url_prefix}/{path.name}",
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "kind": KINDS.get(path.suffix.lower(), path.suffix.lstrip(".").upper() or "File"),
    }


def snapshot(folder: Path) -> dict[str, tuple[float, int]]:
    if not folder.is_dir():
        return {}
    out = {}
    for p in folder.iterdir():
        if p.is_file() and not p.name.startswith("."):
            st = p.stat()
            out[p.name] = (st.st_mtime, st.st_size)
    return out


def changed_since(folder: Path, before: dict[str, tuple[float, int]]) -> list[Path]:
    after = snapshot(folder)
    return sorted((folder / n for n, sig in after.items() if before.get(n) != sig),
                  key=lambda p: p.stat().st_mtime)


def list_files(folder: Path, url_prefix: str) -> list[dict[str, Any]]:
    if not folder.is_dir():
        return []
    files = [describe(p, url_prefix) for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]
    return sorted(files, key=lambda f: f["modified"], reverse=True)


def unique_path(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    return folder / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}{suffix}"


def content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
