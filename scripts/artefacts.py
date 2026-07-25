#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


APPROVED_EXTENSIONS = frozenset({".html", ".png", ".jpeg", ".jpg", ".ico"})
PUBLIC_COMPONENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+)?$")
PROTECTED_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


class ArtefactError(Exception):
    pass


class ManifestError(ArtefactError):
    pass


@dataclass(frozen=True)
class Collection:
    id: str
    title: str
    description: str
    section: str
    section_order: int
    order: int


@dataclass(frozen=True)
class Entry:
    id: str
    source: PurePosixPath
    destination: PurePosixPath
    title: str
    collection: str
    order: int
    replacements: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Manifest:
    version: int
    protected_files: tuple[PurePosixPath, ...]
    collections: tuple[Collection, ...]
    entries: tuple[Entry, ...]


def _safe_relative_path(value: str, field_name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ManifestError(f"{field_name} must be a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ManifestError(f"{field_name} must be a safe relative path")
    return path


def _require_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{name} must be a non-empty string")
    return value


def _require_int(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ManifestError(f"{name} must be an integer")
    return value


def manifest_from_dict(payload: dict[str, Any]) -> Manifest:
    if not isinstance(payload, dict):
        raise ManifestError("manifest must be a JSON object")
    try:
        protected_payload = payload["protected_files"]
        collections_payload = payload["collections"]
        entries_payload = payload["entries"]
    except KeyError as error:
        raise ManifestError(f"missing manifest field: {error.args[0]}") from error
    if not isinstance(protected_payload, list):
        raise ManifestError("protected_files must be an array")
    if not isinstance(collections_payload, list):
        raise ManifestError("collections must be an array")
    if not isinstance(entries_payload, list):
        raise ManifestError("entries must be an array")

    protected_files = tuple(
        _safe_relative_path(value, "protected file") for value in protected_payload
    )
    collections = tuple(
        Collection(
            id=_require_string(item, "id"),
            title=_require_string(item, "title"),
            description=_require_string(item, "description"),
            section=_require_string(item, "section"),
            section_order=_require_int(item, "section_order"),
            order=_require_int(item, "order"),
        )
        for item in collections_payload
        if isinstance(item, dict)
    )
    if len(collections) != len(collections_payload):
        raise ManifestError("each collection must be an object")

    entries: list[Entry] = []
    for item in entries_payload:
        if not isinstance(item, dict):
            raise ManifestError("each entry must be an object")
        replacements = item.get("replacements", {})
        if not isinstance(replacements, dict) or not all(
            isinstance(old, str)
            and old
            and isinstance(new, str)
            and new
            for old, new in replacements.items()
        ):
            raise ManifestError("replacements must map non-empty strings")
        entries.append(
            Entry(
                id=_require_string(item, "id"),
                source=_safe_relative_path(_require_string(item, "source"), "source"),
                destination=_safe_relative_path(
                    _require_string(item, "destination"), "destination"
                ),
                title=_require_string(item, "title"),
                collection=_require_string(item, "collection"),
                order=_require_int(item, "order"),
                replacements=dict(replacements),
            )
        )

    manifest = Manifest(
        version=payload.get("version"),
        protected_files=protected_files,
        collections=collections,
        entries=tuple(entries),
    )
    return manifest


def _require_unique(values: list[Any], message: str) -> None:
    if len(values) != len(set(values)):
        raise ManifestError(message)


def _validate_public_path(path: PurePosixPath, field_name: str) -> None:
    if not all(PUBLIC_COMPONENT.fullmatch(component) for component in path.parts):
        raise ManifestError(f"{field_name} must be lowercase kebab-case")


def validate_manifest(manifest: Manifest) -> None:
    if manifest.version != 1:
        raise ManifestError("version must be 1")

    _require_unique(
        [collection.id for collection in manifest.collections],
        "duplicate collection id",
    )
    _require_unique([entry.id for entry in manifest.entries], "duplicate entry id")
    _require_unique(
        [entry.destination for entry in manifest.entries], "duplicate destination"
    )
    _require_unique(list(manifest.protected_files), "duplicate protected file")
    _require_unique(
        [
            (collection.section_order, collection.order)
            for collection in manifest.collections
        ],
        "duplicate collection order",
    )
    _require_unique(
        [(entry.collection, entry.order) for entry in manifest.entries],
        "duplicate entry order",
    )

    collection_ids = {collection.id for collection in manifest.collections}
    for entry in manifest.entries:
        if entry.collection not in collection_ids:
            raise ManifestError(f"unknown collection for entry {entry.id}")
        source_suffix = entry.source.suffix.lower()
        destination_suffix = entry.destination.suffix.lower()
        if source_suffix not in APPROVED_EXTENSIONS:
            raise ManifestError(f"unsupported source extension for entry {entry.id}")
        _validate_public_path(entry.destination, "destination")
        if source_suffix == ".html":
            if entry.destination.name != "index.html":
                raise ManifestError(
                    f"HTML destination for entry {entry.id} must end in index.html"
                )
        elif destination_suffix != source_suffix:
            raise ManifestError(
                f"image destination for entry {entry.id} must keep source extension"
            )

    for path in manifest.protected_files:
        if not all(PROTECTED_COMPONENT.fullmatch(component) for component in path.parts):
            raise ManifestError("protected file must use a lowercase safe path")


def load_manifest(path: Path) -> Manifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read manifest: {error}") from error
    manifest = manifest_from_dict(payload)
    validate_manifest(manifest)
    return manifest
