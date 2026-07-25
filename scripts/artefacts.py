#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import html
import json
import os
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


class InventoryError(ArtefactError):
    pass


class TransformationError(ArtefactError):
    pass


class CatalogueError(ArtefactError):
    pass


CATALOGUE_START = "<!-- ARTEFACTS:START -->"
CATALOGUE_END = "<!-- ARTEFACTS:END -->"


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


@dataclass(frozen=True)
class SourceInventory:
    approved: tuple[PurePosixPath, ...]
    excluded_suffixes: tuple[str, ...]


@dataclass(frozen=True)
class SourceReconciliation:
    next_manifest: Manifest
    missing_entries: tuple[Entry, ...]
    excluded_suffixes: tuple[str, ...]


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


def scan_source(source_root: Path) -> SourceInventory:
    if not source_root.is_dir():
        raise InventoryError(f"source directory does not exist: {source_root}")
    resolved_root = source_root.resolve()
    approved: list[PurePosixPath] = []
    excluded_suffixes: set[str] = set()

    for current_root, directory_names, file_names in os.walk(source_root):
        current_path = Path(current_root)
        for name in (*directory_names, *file_names):
            candidate = current_path / name
            if candidate.is_symlink():
                raise InventoryError(f"symbolic link is not allowed: {candidate}")
        directory_names[:] = [
            name
            for name in directory_names
            if not name.startswith(".") and name != "kevinlin.github.io"
        ]
        for name in file_names:
            if name == ".DS_Store":
                continue
            candidate = current_path / name
            suffix = candidate.suffix.lower()
            if suffix not in APPROVED_EXTENSIONS:
                if suffix:
                    excluded_suffixes.add(suffix)
                continue
            resolved = candidate.resolve()
            if not resolved.is_relative_to(resolved_root):
                raise InventoryError(f"source path escapes source directory: {candidate}")
            approved.append(PurePosixPath(candidate.relative_to(source_root).as_posix()))

    return SourceInventory(
        approved=tuple(sorted(approved, key=str)),
        excluded_suffixes=tuple(sorted(excluded_suffixes)),
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise InventoryError(f"cannot normalize path component: {value}")
    return slug


def suggest_destination(source: PurePosixPath) -> PurePosixPath:
    parent_parts = tuple(_slug(part) for part in source.parent.parts if part != ".")
    stem = _slug(source.stem)
    suffix = source.suffix.lower()
    if suffix == ".html":
        return PurePosixPath(*parent_parts, stem, "index.html")
    return PurePosixPath(*parent_parts, f"{stem}{suffix}")


def reconcile_inventory(
    manifest: Manifest, inventory: SourceInventory
) -> SourceReconciliation:
    approved = set(inventory.approved)
    manifest_sources = {entry.source for entry in manifest.entries}
    unlisted = sorted(approved - manifest_sources, key=str)
    if unlisted:
        suggestions = "\n".join(
            f"  {source.as_posix()} -> {suggest_destination(source).as_posix()}"
            for source in unlisted
        )
        raise InventoryError(f"unlisted approved source files:\n{suggestions}")
    missing = tuple(entry for entry in manifest.entries if entry.source not in approved)
    next_manifest = replace(
        manifest,
        entries=tuple(entry for entry in manifest.entries if entry.source in approved),
    )
    return SourceReconciliation(
        next_manifest=next_manifest,
        missing_entries=missing,
        excluded_suffixes=inventory.excluded_suffixes,
    )


def transform_html(entry: Entry, source_bytes: bytes) -> bytes:
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TransformationError(f"HTML source is not UTF-8: {entry.source}") from error
    for old, new in entry.replacements.items():
        if old not in text:
            raise TransformationError(
                f"expected replacement not found for {entry.id}: {old}"
            )
        text = text.replace(old, new)
    text = re.sub(r"[ \t]+(?=\r?$)", "", text, flags=re.MULTILINE)
    if "cdnjs.cloudflare.com" in text or "https://cdnjs" in text:
        raise TransformationError(f"forbidden cdnjs reference remains in {entry.id}")
    if text and not text.endswith(("\n", "\r")):
        text += "\n"
    return text.encode("utf-8")


def build_desired_files(
    manifest: Manifest, source_root: Path
) -> dict[PurePosixPath, bytes]:
    resolved_root = source_root.resolve()
    desired: dict[PurePosixPath, bytes] = {}
    for entry in manifest.entries:
        source_path = source_root / entry.source.as_posix()
        if not source_path.exists():
            continue
        if source_path.is_symlink():
            raise InventoryError(f"symbolic link is not allowed: {source_path}")
        if not source_path.resolve().is_relative_to(resolved_root):
            raise InventoryError(f"source path escapes source directory: {source_path}")
        source_bytes = source_path.read_bytes()
        if entry.source.suffix.lower() == ".html":
            output = transform_html(entry, source_bytes)
        else:
            output = source_bytes
            if hashlib.sha256(output).digest() != hashlib.sha256(source_bytes).digest():
                raise TransformationError(f"binary hash mismatch for {entry.id}")
        desired[entry.destination] = output
    return desired


def public_href(destination: PurePosixPath) -> str:
    if destination.name == "index.html":
        return destination.parent.as_posix().rstrip("/") + "/"
    return destination.as_posix()


def render_catalogue(manifest: Manifest) -> str:
    entries_by_collection: dict[str, list[Entry]] = {}
    for entry in manifest.entries:
        entries_by_collection.setdefault(entry.collection, []).append(entry)

    sections: dict[tuple[int, str], list[Collection]] = {}
    for collection in manifest.collections:
        if entries_by_collection.get(collection.id):
            sections.setdefault(
                (collection.section_order, collection.section), []
            ).append(collection)

    lines: list[str] = []
    for (_, section_title), collections in sorted(sections.items()):
        heading_id = f"{_slug(section_title)}-heading"
        lines.extend(
            [
                f'        <section aria-labelledby="{heading_id}">',
                f'            <h2 id="{heading_id}">{html.escape(section_title)}</h2>',
                '            <div class="card-grid">',
            ]
        )
        for collection in sorted(collections, key=lambda item: item.order):
            lines.extend(
                [
                    '                <article class="card">',
                    f"                    <h3>{html.escape(collection.title)}</h3>",
                    f"                    <p>{html.escape(collection.description)}</p>",
                    "                    <ul>",
                ]
            )
            for entry in sorted(
                entries_by_collection[collection.id], key=lambda item: item.order
            ):
                href = html.escape(public_href(entry.destination), quote=True)
                title = html.escape(entry.title)
                lines.append(f'                        <li><a href="{href}">{title}</a></li>')
            lines.extend(
                [
                    "                    </ul>",
                    "                </article>",
                ]
            )
        lines.extend(["            </div>", "        </section>"])
    return "\n".join(lines)


def replace_generated_catalogue(document: str, generated: str) -> str:
    if document.count(CATALOGUE_START) != 1 or document.count(CATALOGUE_END) != 1:
        raise CatalogueError("catalogue must contain exactly one marker pair")
    start = document.index(CATALOGUE_START) + len(CATALOGUE_START)
    end = document.index(CATALOGUE_END)
    if start > end:
        raise CatalogueError("catalogue markers are out of order")
    end_line_start = document.rfind("\n", 0, end) + 1
    indentation = document[end_line_start:end]
    if indentation.strip():
        raise CatalogueError("end marker must start on its own line")
    return document[:start] + "\n" + generated + "\n" + indentation + document[end:]
