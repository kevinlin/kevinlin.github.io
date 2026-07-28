#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlsplit


APPROVED_EXTENSIONS = frozenset({".html", ".png", ".jpeg", ".jpg", ".ico"})
PUBLIC_COMPONENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+)?$")
PROTECTED_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
CDNJS_HOST = "cdnjs.cloudflare.com"
IGNORED_METADATA_NAME = ".DS_Store"
DELETION_KINDS = frozenset({"delete", "orphan"})


def has_cdnjs_reference(text: str) -> bool:
    return CDNJS_HOST in text or "https://cdnjs" in text


class ArtefactError(Exception):
    pass


class ManifestError(ArtefactError):
    pass


class InventoryError(ArtefactError):
    pass


class UnlistedSourceError(InventoryError):
    def __init__(
        self, message: str, manifest: Manifest, unlisted: tuple[PurePosixPath, ...]
    ) -> None:
        super().__init__(message)
        self.manifest = manifest
        self.unlisted = unlisted


class TransformationError(ArtefactError):
    pass


class CatalogueError(ArtefactError):
    pass


class ValidationError(ArtefactError):
    pass


class PublishError(ArtefactError):
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
class ManifestProposal:
    collections: tuple[Collection, ...]
    entries: tuple[Entry, ...]
    warnings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceReconciliation:
    next_manifest: Manifest
    missing_entries: tuple[Entry, ...]
    excluded_suffixes: tuple[str, ...]


@dataclass(frozen=True)
class Change:
    kind: str
    destination: PurePosixPath


@dataclass(frozen=True)
class SyncPlan:
    manifest: Manifest
    next_manifest: Manifest
    desired_files: dict[PurePosixPath, bytes]
    changes: tuple[Change, ...]
    unchanged: tuple[PurePosixPath, ...]
    excluded_suffixes: tuple[str, ...]


@dataclass(frozen=True)
class ValidationReport:
    entry_count: int
    local_link_count: int
    ignored_metadata_count: int
    homepage_unchanged: bool


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


CommandRunner = Callable[[list[str], Path], CommandResult]


@dataclass(frozen=True)
class PublishResult:
    pull_request_url: str
    merge_commit: str
    catalogue_url: str
    verified_url_count: int
    excluded_suffixes: tuple[str, ...]


class _ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is None:
                continue
            if name == "href":
                self.hrefs.append(value)
                self.references.append(value)
            elif name == "src":
                self.references.append(value)


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
    _require_unique([entry.source for entry in manifest.entries], "duplicate source")
    _require_unique(
        [entry.destination for entry in manifest.entries], "duplicate destination"
    )
    _require_unique(list(manifest.protected_files), "duplicate protected file")

    collection_ids = {collection.id for collection in manifest.collections}
    reserved_destinations = {PurePosixPath("index.html"), PurePosixPath("manifest.json")}
    managed_destinations = {entry.destination for entry in manifest.entries}
    protected_destinations = set(manifest.protected_files)
    if managed_destinations & reserved_destinations:
        raise ManifestError("entry destination is reserved")
    if protected_destinations & reserved_destinations:
        raise ManifestError("protected file destination is reserved")
    if protected_destinations & managed_destinations:
        raise ManifestError("protected and managed destinations must be disjoint")
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


def _renumber_colliding_orders(items: tuple[Any, ...], group_of) -> tuple[Any, ...]:
    """Reassign 10, 20, 30 … inside any group whose declared orders collide.

    Order is hand-edited manifest content, so a group that already reads
    unambiguously keeps its numbers and its gaps. A group with a collision is
    renumbered in the sequence it already renders in — declared order first,
    manifest position as the tie-break — so a pasted duplicate lands beside the
    item it was copied from rather than at the end of the group.
    """
    groups: dict[Any, list[int]] = {}
    for index, item in enumerate(items):
        groups.setdefault(group_of(item), []).append(index)
    renumbered = list(items)
    for indices in groups.values():
        orders = [items[index].order for index in indices]
        if len(set(orders)) == len(orders):
            continue
        ordered = sorted(indices, key=lambda index: (items[index].order, index))
        for position, index in enumerate(ordered, start=1):
            renumbered[index] = replace(items[index], order=position * 10)
    return tuple(renumbered)


def normalize_orders(manifest: Manifest) -> Manifest:
    """Give the catalogue one definite sequence when declared orders collide.

    Duplicate order values used to abort every command, leaving the user to
    hand-number a merged or pasted block against a schema the script already
    knows. Ordering is bookkeeping: `plan` resolves it and shows the rewritten
    manifest as a normal change.
    """
    return replace(
        manifest,
        collections=_renumber_colliding_orders(
            manifest.collections, lambda collection: collection.section
        ),
        entries=_renumber_colliding_orders(
            manifest.entries, lambda entry: entry.collection
        ),
    )


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
            if name == IGNORED_METADATA_NAME:
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


PRESENTATION_SECTION = "Presentations and analysis"
IMAGE_SECTION = "Image collections"
PLACEHOLDER_DESCRIPTION = "TODO: describe this collection."
CDNJS_REFERENCE = re.compile(rf"https://{re.escape(CDNJS_HOST)}/[^\s\"'<>)]+")


def _normalize_words(stem: str) -> str:
    text = re.sub(r"^\d+[-_ ]+", "", stem)
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise InventoryError(f"cannot derive a title from: {stem}")
    return text


def _derive_title(stem: str) -> str:
    text = _normalize_words(stem)
    return text[0].upper() + text[1:]


def _unique_id(base: str, taken: set[str]) -> str:
    candidate = base
    suffix = 1
    while candidate in taken:
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def _derive_entry_id(destination: PurePosixPath, taken: set[str]) -> str:
    if destination.name == "index.html":
        parts = destination.parent.parts
    else:
        parts = (*destination.parent.parts, destination.stem)
    return _unique_id(_slug("-".join(parts)), taken)


def _source_group(source: PurePosixPath) -> str:
    return source.parts[0] if len(source.parts) > 1 else ""


def _max_orders(items, group_attr: str) -> dict[str, int]:
    """Highest declared order per group, used to continue existing numbering."""
    result: dict[str, int] = {}
    for item in items:
        key = getattr(item, group_attr)
        result[key] = max(result.get(key, 0), item.order)
    return result


def _sections_by_media(manifest: Manifest) -> dict[bool, str]:
    """Section each media type already sits in, keyed by "holds a presentation".

    Section names are manifest content, so they are learned from the existing
    collections rather than matched against the constants below; a renamed
    section must not make the next proposal invent a second one beside it.
    """
    presentation_collections = {
        entry.collection
        for entry in manifest.entries
        if entry.destination.suffix.lower() == ".html"
    }
    observed: dict[bool, str] = {}
    for collection in manifest.collections:
        observed.setdefault(collection.id in presentation_collections, collection.section)
    return observed


def _vendor_key(name: str) -> str:
    """File name with the `.min` build marker dropped.

    A page can load `chart.umd.js` while the repository vendors
    `chart.umd.min.js`. Same library, same API, different build, so the vendored
    copy is the substitute the user would pick by hand; an exact name match
    misses it and leaves the entry to fail later in `transform_html`.
    """
    return name.replace(".min.", ".", 1)


def _vendor_replacements(
    vendor_by_name: dict[str, PurePosixPath],
    source_path: Path,
    destination: PurePosixPath,
) -> tuple[dict[str, str], bool]:
    """Pre-filled replacements, and whether an unmapped cdnjs reference survives them.

    `CDNJS_REFERENCE` matches raw text because `transform_html` replaces raw
    text; the parsed references from `_parse_references` are HTML-unescaped and
    would not always be found. Rather than trust the match to be exhaustive, the
    replacements are applied here exactly as `transform_html` will apply them and
    the result is put through the same `has_cdnjs_reference` ban check, so a
    reference this function missed is reported now instead of killing the re-run.
    """
    try:
        text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise InventoryError(f"cannot read HTML source {source_path}: {error}") from error
    prefix = "../" * len(destination.parent.parts)
    replacements: dict[str, str] = {}
    for url in CDNJS_REFERENCE.findall(text):
        vendor = vendor_by_name.get(_vendor_key(url.rsplit("/", 1)[-1]))
        if vendor is not None:
            replacements[url] = prefix + vendor.as_posix()
    for old, new in replacements.items():
        text = text.replace(old, new)
    return replacements, has_cdnjs_reference(text)


def propose_manifest_additions(
    manifest: Manifest,
    unlisted: tuple[PurePosixPath, ...],
    source_root: Path,
) -> ManifestProposal:
    """Derive schema-valid manifest additions for approved sources with no entry."""
    collection_by_group = {
        _source_group(entry.source): entry.collection for entry in manifest.entries
    }
    section_orders = {
        collection.section: collection.section_order for collection in manifest.collections
    }
    order_in_section = _max_orders(manifest.collections, "section")
    order_in_collection = _max_orders(manifest.entries, "collection")
    taken_ids = {entry.id for entry in manifest.entries}
    collection_ids = {collection.id for collection in manifest.collections}
    vendor_by_name = {_vendor_key(path.name): path for path in manifest.protected_files}
    sections_by_media = _sections_by_media(manifest)

    grouped: dict[str, list[PurePosixPath]] = {}
    for source in sorted(unlisted, key=str):
        grouped.setdefault(_source_group(source), []).append(source)

    collections: list[Collection] = []
    entries: list[Entry] = []
    warnings: dict[str, str] = {}
    for group, sources in grouped.items():
        collection_id = collection_by_group.get(group)
        if collection_id is None:
            label = group or sources[0].stem
            collection_id = _unique_id(_slug(label), collection_ids)
            collection_ids.add(collection_id)
            is_presentation = any(source.suffix.lower() == ".html" for source in sources)
            section = sections_by_media.get(
                is_presentation,
                PRESENTATION_SECTION if is_presentation else IMAGE_SECTION,
            )
            section_orders.setdefault(
                section, max(section_orders.values(), default=0) + 10
            )
            order_in_section[section] = order_in_section.get(section, 0) + 10
            collections.append(
                Collection(
                    id=collection_id,
                    title=_normalize_words(label).title(),
                    description=PLACEHOLDER_DESCRIPTION,
                    section=section,
                    section_order=section_orders[section],
                    order=order_in_section[section],
                )
            )

        for source in sources:
            destination = suggest_destination(source)
            entry_id = _derive_entry_id(destination, taken_ids)
            taken_ids.add(entry_id)
            order_in_collection[collection_id] = (
                order_in_collection.get(collection_id, 0) + 10
            )
            if source.suffix.lower() == ".html":
                replacements, unmapped = _vendor_replacements(
                    vendor_by_name, source_root / source.as_posix(), destination
                )
                if unmapped:
                    warnings[entry_id] = (
                        "an unmapped cdnjs reference remains; vendor it into "
                        "protected_files or the next run fails in transform_html"
                    )
            else:
                replacements = {}
            entries.append(
                Entry(
                    id=entry_id,
                    source=source,
                    destination=destination,
                    title=_derive_title(source.stem),
                    collection=collection_id,
                    order=order_in_collection[collection_id],
                    replacements=replacements,
                )
            )

    return ManifestProposal(
        collections=tuple(collections), entries=tuple(entries), warnings=warnings
    )


def merge_manifest_proposal(manifest: Manifest, proposal: ManifestProposal) -> Manifest:
    merged = replace(
        manifest,
        collections=(*manifest.collections, *proposal.collections),
        entries=(*manifest.entries, *proposal.entries),
    )
    validate_manifest(merged)
    return merged


def format_proposal(proposal: ManifestProposal) -> str:
    lines = [f"Proposed collections ({len(proposal.collections)})"]
    for collection in proposal.collections:
        lines.append(
            f"  + {collection.id}: {collection.title} "
            f"[{collection.section} {collection.section_order}/{collection.order}]"
        )
        lines.append(f"      description: {collection.description}")
    lines.append(f"Proposed entries ({len(proposal.entries)})")
    for entry in proposal.entries:
        lines.append(f"  + {entry.id}")
        lines.append(f"      source:      {entry.source.as_posix()}")
        lines.append(f"      destination: {entry.destination.as_posix()}")
        lines.append(f"      title:       {entry.title}")
        lines.append(f"      collection:  {entry.collection} (order {entry.order})")
        for old, new in entry.replacements.items():
            lines.append(f"      replace:     {old} -> {new}")
        warning = proposal.warnings.get(entry.id)
        if warning is not None:
            lines.append(f"      WARNING:     {warning}")
    return "\n".join(lines)


def reconcile_inventory(
    manifest: Manifest, inventory: SourceInventory
) -> SourceReconciliation:
    approved = set(inventory.approved)
    manifest_sources = {entry.source for entry in manifest.entries}
    unlisted = sorted(approved - manifest_sources, key=str)
    if unlisted:
        raise UnlistedSourceError(
            "unlisted approved source files", manifest, tuple(unlisted)
        )
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
    if has_cdnjs_reference(text):
        remaining = ", ".join(dict.fromkeys(CDNJS_REFERENCE.findall(text))) or CDNJS_HOST
        raise TransformationError(
            f"forbidden cdnjs reference remains in {entry.id}: {remaining}"
        )
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


def manifest_to_json(manifest: Manifest) -> bytes:
    payload = {
        "version": manifest.version,
        "protected_files": [path.as_posix() for path in manifest.protected_files],
        "collections": [
            {
                "id": collection.id,
                "title": collection.title,
                "description": collection.description,
                "section": collection.section,
                "section_order": collection.section_order,
                "order": collection.order,
            }
            for collection in manifest.collections
        ],
        "entries": [
            {
                "id": entry.id,
                "source": entry.source.as_posix(),
                "destination": entry.destination.as_posix(),
                "title": entry.title,
                "collection": entry.collection,
                "order": entry.order,
                "replacements": entry.replacements,
            }
            for entry in manifest.entries
        ],
    }
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def read_head_manifest(repo_root: Path) -> bytes | None:
    result = subprocess.run(
        ["git", "show", "HEAD:artefacts/manifest.json"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def manifest_from_bytes(content: bytes, description: str) -> Manifest:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read {description} manifest: {error}") from error
    manifest = manifest_from_dict(payload)
    validate_manifest(manifest)
    return manifest


def _validate_desired_tree(
    repo_root: Path,
    artefacts_root: Path,
    manifest: Manifest,
    desired_files: dict[PurePosixPath, bytes],
) -> None:
    with tempfile.TemporaryDirectory(prefix="artefact-plan-") as directory:
        planned_repo = Path(directory)
        for name in ("index.html", "styles.css", "script.js"):
            source = repo_root / name
            if source.is_file():
                (planned_repo / name).write_bytes(source.read_bytes())
        planned_artefacts = planned_repo / "artefacts"
        planned_artefacts.mkdir()
        for destination in manifest.protected_files:
            source = artefacts_root / destination.as_posix()
            if not source.is_file() or source.is_symlink():
                raise ValidationError(f"missing protected file: {destination}")
            target = planned_artefacts / destination.as_posix()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        for destination, content in desired_files.items():
            target = planned_artefacts / destination.as_posix()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        validate_repository(planned_repo, None)


def scan_published_tree(artefacts_root: Path) -> tuple[set[PurePosixPath], int]:
    """Published files under `artefacts/`, plus the count of ignored metadata files."""
    published: set[PurePosixPath] = set()
    ignored_metadata = 0
    for path in artefacts_root.rglob("*"):
        if not path.is_file():
            continue
        if path.name == IGNORED_METADATA_NAME:
            ignored_metadata += 1
            continue
        published.add(PurePosixPath(path.relative_to(artefacts_root).as_posix()))
    return published, ignored_metadata


def unexpected_published_files(
    published: set[PurePosixPath], expected: set[PurePosixPath]
) -> list[PurePosixPath]:
    """Published files the manifest does not explain.

    One formula for `validate`'s rejection and the plan's orphan sweep, so the plan
    cannot propose a tree that `validate` then refuses.
    """
    return sorted(published - expected, key=str)


def create_sync_plan(
    manifest_path: Path,
    source_root: Path,
    artefacts_root: Path,
    head_manifest: bytes | None,
) -> SyncPlan:
    declared = load_manifest(manifest_path)
    manifest = normalize_orders(declared)
    inventory = scan_source(source_root)
    reconciliation = reconcile_inventory(manifest, inventory)
    next_manifest = reconciliation.next_manifest
    desired_files = build_desired_files(next_manifest, source_root)

    catalogue_path = artefacts_root / "index.html"
    try:
        catalogue = catalogue_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CatalogueError(f"cannot read catalogue: {error}") from error
    generated_catalogue = replace_generated_catalogue(
        catalogue, render_catalogue(next_manifest)
    ).encode("utf-8")
    desired_files[PurePosixPath("index.html")] = generated_catalogue
    desired_files[PurePosixPath("manifest.json")] = manifest_to_json(next_manifest)
    _validate_desired_tree(
        artefacts_root.parent.resolve(), artefacts_root, next_manifest, desired_files
    )

    changes: list[Change] = []
    unchanged: list[PurePosixPath] = []
    for destination, content in sorted(desired_files.items(), key=lambda item: str(item[0])):
        current_path = artefacts_root / destination.as_posix()
        if destination == PurePosixPath("manifest.json") and head_manifest is not None:
            current = head_manifest
            exists = True
        else:
            exists = current_path.is_file()
            current = current_path.read_bytes() if exists else None
        if not exists:
            changes.append(Change("add", destination))
        elif current != content:
            changes.append(Change("update", destination))
        else:
            unchanged.append(destination)

    deletion_candidates = {entry.destination for entry in reconciliation.missing_entries}
    if head_manifest is not None:
        previous_manifest = manifest_from_bytes(head_manifest, "HEAD")
        next_destinations = {entry.destination for entry in next_manifest.entries}
        deletion_candidates.update(
            entry.destination
            for entry in previous_manifest.entries
            if entry.destination not in next_destinations
        )
    retained_destinations = {
        *next_manifest.protected_files,
        PurePosixPath("index.html"),
        PurePosixPath("manifest.json"),
        *(change.destination for change in changes),
    }
    for destination in sorted(deletion_candidates - retained_destinations, key=str):
        destination_path = artefacts_root / destination.as_posix()
        if destination_path.exists():
            changes.append(Change("delete", destination))

    published, _ = scan_published_tree(artefacts_root)
    planned_deletions = {change.destination for change in changes if change.kind == "delete"}
    expected = {*desired_files, *next_manifest.protected_files}
    for destination in unexpected_published_files(published, expected):
        if destination not in planned_deletions:
            changes.append(Change("orphan", destination))

    return SyncPlan(
        manifest=declared,
        next_manifest=next_manifest,
        desired_files=desired_files,
        changes=tuple(sorted(changes, key=lambda item: (item.kind, str(item.destination)))),
        unchanged=tuple(sorted(unchanged, key=str)),
        excluded_suffixes=reconciliation.excluded_suffixes,
    )


def _renumbered_orders(plan: SyncPlan) -> list[str]:
    """Order values `normalize_orders` rewrote, so the rewrite is never silent."""
    declared_collections = {
        collection.id: collection.order for collection in plan.manifest.collections
    }
    declared_entries = {entry.id: entry.order for entry in plan.manifest.entries}
    labels = [
        f"{collection.id}: {declared_collections[collection.id]} -> {collection.order}"
        for collection in plan.next_manifest.collections
        if declared_collections.get(collection.id, collection.order) != collection.order
    ]
    labels.extend(
        f"{entry.id}: {declared_entries[entry.id]} -> {entry.order}"
        for entry in plan.next_manifest.entries
        if declared_entries.get(entry.id, entry.order) != entry.order
    )
    return labels


def format_plan(plan: SyncPlan) -> str:
    symbols = {"add": "+", "update": "~", "delete": "-", "orphan": "-"}
    headings = {
        "add": "Add",
        "update": "Update",
        "delete": "Delete",
        "orphan": "Delete (orphaned)",
    }
    lines: list[str] = []
    for kind in ("add", "update", "delete", "orphan"):
        changes = sorted(
            (change for change in plan.changes if change.kind == kind),
            key=lambda item: str(item.destination),
        )
        lines.append(f"{headings[kind]} ({len(changes)})")
        lines.extend(
            f"  {symbols[kind]} {change.destination.as_posix()}" for change in changes
        )
    renumbered = _renumbered_orders(plan)
    if renumbered:
        lines.append(f"Renumbered order ({len(renumbered)})")
        lines.extend(f"  ~ {label}" for label in renumbered)
    lines.append(f"Unchanged ({len(plan.unchanged)})")
    excluded = ", ".join(plan.excluded_suffixes) if plan.excluded_suffixes else "none"
    lines.append(f"Excluded source types: {excluded}")
    return "\n".join(lines)


def _destination_path(artefacts_root: Path, destination: PurePosixPath) -> Path:
    root = artefacts_root.resolve()
    target = artefacts_root / destination.as_posix()
    if not target.resolve(strict=False).is_relative_to(root):
        raise ArtefactError(f"destination escapes artefacts directory: {destination}")
    current = artefacts_root
    for component in destination.parts[:-1]:
        current /= component
        if current.is_symlink():
            raise ArtefactError(f"destination parent is a symbolic link: {destination}")
    return target


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def apply_plan(plan: SyncPlan, manifest_path: Path, artefacts_root: Path) -> None:
    for change in plan.changes:
        if change.kind not in {"add", "update"}:
            continue
        target = _destination_path(artefacts_root, change.destination)
        _atomic_write(target, plan.desired_files[change.destination])

    for change in plan.changes:
        if change.kind not in DELETION_KINDS:
            continue
        target = _destination_path(artefacts_root, change.destination)
        if target.exists():
            if not target.is_file() or target.is_symlink():
                raise ArtefactError(f"refusing to delete non-file destination: {change.destination}")
            target.unlink()
        parent = target.parent
        while parent != artefacts_root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    for destination, expected in plan.desired_files.items():
        target = _destination_path(artefacts_root, destination)
        if not target.is_file() or target.read_bytes() != expected:
            raise ArtefactError(f"applied file differs from plan: {destination}")
    for change in plan.changes:
        if change.kind in DELETION_KINDS and _destination_path(
            artefacts_root, change.destination
        ).exists():
            raise ArtefactError(f"deleted file remains after apply: {change.destination}")
    if manifest_path.read_bytes() != plan.desired_files[PurePosixPath("manifest.json")]:
        raise ArtefactError("applied manifest differs from plan")


def _parse_references(path: Path) -> _ReferenceParser:
    parser = _ReferenceParser()
    try:
        parser.feed(path.read_text(encoding="utf-8"))
        parser.close()
    except (OSError, UnicodeError) as error:
        raise ValidationError(f"cannot parse HTML file {path}: {error}") from error
    return parser


def _resolve_local_reference(repo_root: Path, page: Path, reference: str) -> Path | None:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    reference_path = unquote(parsed.path)
    if reference_path.startswith("/"):
        target = repo_root / reference_path.lstrip("/")
    else:
        target = page.parent / reference_path
    if reference_path.endswith("/") or target.is_dir():
        target /= "index.html"
    resolved = target.resolve(strict=False)
    if not resolved.is_relative_to(repo_root.resolve()):
        raise ValidationError(f"local reference escapes repository: {reference}")
    return resolved


def validate_repository(repo_root: Path, base_ref: str | None) -> ValidationReport:
    repo_root = repo_root.resolve()
    artefacts_root = repo_root / "artefacts"
    manifest = load_manifest(artefacts_root / "manifest.json")
    expected = {
        PurePosixPath("index.html"),
        PurePosixPath("manifest.json"),
        *manifest.protected_files,
        *(entry.destination for entry in manifest.entries),
    }
    actual, ignored_metadata = scan_published_tree(artefacts_root)
    missing = sorted(expected - actual, key=str)
    unexpected = unexpected_published_files(actual, expected)
    if missing:
        raise ValidationError(
            "missing published file: " + ", ".join(path.as_posix() for path in missing)
        )
    if unexpected:
        raise ValidationError(
            "unexpected published file: "
            + ", ".join(path.as_posix() for path in unexpected)
        )

    catalogue_parser = _parse_references(artefacts_root / "index.html")
    for entry in manifest.entries:
        href = public_href(entry.destination)
        count = catalogue_parser.hrefs.count(href)
        if count != 1:
            raise ValidationError(
                f"catalogue link for {entry.id} must appear exactly once, found {count}"
            )

    local_targets: set[Path] = set()
    for relative_path in sorted(actual, key=str):
        if relative_path.suffix != ".html":
            continue
        page = artefacts_root / relative_path.as_posix()
        text = page.read_text(encoding="utf-8")
        if has_cdnjs_reference(text):
            raise ValidationError(f"forbidden cdnjs reference in {relative_path}")
        parser = _parse_references(page)
        for reference in parser.references:
            target = _resolve_local_reference(repo_root, page, reference)
            if target is None:
                continue
            if not target.is_file():
                raise ValidationError(
                    f"broken local reference in {relative_path}: {reference}"
                )
            local_targets.add(target)

    homepage_unchanged = True
    if base_ref is not None:
        result = subprocess.run(
            [
                "git",
                "diff",
                "--exit-code",
                f"{base_ref}...HEAD",
                "--",
                "index.html",
                "styles.css",
                "script.js",
            ],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValidationError("homepage files changed relative to base ref")
    return ValidationReport(
        entry_count=len(manifest.entries),
        local_link_count=len(local_targets),
        ignored_metadata_count=ignored_metadata,
        homepage_unchanged=homepage_unchanged,
    )


def subprocess_runner(args: list[str], cwd: Path) -> CommandResult:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(result.stdout, result.stderr, result.returncode)


def _run_checked(
    runner: CommandRunner, args: list[str], cwd: Path, failure: str
) -> str:
    result = runner(args, cwd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise PublishError(f"{failure}{suffix}")
    return result.stdout


def _publish_preflight(repo_root: Path, runner: CommandRunner) -> None:
    for command in (["git", "--version"], ["gh", "--version"], ["curl", "--version"]):
        _run_checked(runner, command, repo_root, f"required command unavailable: {command[0]}")
    _run_checked(runner, ["gh", "auth", "status"], repo_root, "GitHub CLI is not authenticated")

    status = _run_checked(
        runner, ["git", "status", "--porcelain"], repo_root, "cannot read working tree"
    )
    status_lines = [line for line in status.splitlines() if line]
    if status_lines not in ([], [" M artefacts/manifest.json"]):
        raise PublishError(
            "working tree must be clean or contain only one unstaged artefacts/manifest.json edit"
        )
    branch = _run_checked(
        runner, ["git", "branch", "--show-current"], repo_root, "cannot read branch"
    ).strip()
    if branch != "main":
        raise PublishError("publish must start on branch main")
    _run_checked(runner, ["git", "fetch", "origin", "main"], repo_root, "cannot fetch origin/main")
    divergence = _run_checked(
        runner,
        ["git", "rev-list", "--left-right", "--count", "main...origin/main"],
        repo_root,
        "cannot compare main with origin/main",
    ).split()
    if divergence != ["0", "0"]:
        raise PublishError("local main must be up to date with origin/main")


def _head_manifest_with_runner(repo_root: Path, runner: CommandRunner) -> bytes | None:
    result = runner(["git", "show", "HEAD:artefacts/manifest.json"], repo_root)
    if result.returncode != 0:
        return None
    return result.stdout.encode("utf-8")


def _require_successful_checks(
    repo_root: Path,
    pull_request_url: str,
    runner: CommandRunner,
    sleeper: Callable[[float], None],
) -> None:
    checks: list[dict[str, Any]] | None = None
    for _ in range(60):
        result = runner(
            ["gh", "pr", "checks", pull_request_url, "--json", "name,bucket"],
            repo_root,
        )
        if result.returncode == 0:
            try:
                parsed = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                raise PublishError("cannot parse GitHub checks") from error
            if not isinstance(parsed, list):
                raise PublishError("cannot parse GitHub checks")
            checks = parsed
            if any(check.get("name") == "validate" for check in checks):
                break
        sleeper(2)
    else:
        raise PublishError("required validate check is missing; pull request remains open")

    watch = runner(
        ["gh", "pr", "checks", pull_request_url, "--watch", "--fail-fast"],
        repo_root,
    )
    if watch.returncode != 0:
        raise PublishError("GitHub checks failed; pull request remains open")
    output = _run_checked(
        runner,
        ["gh", "pr", "checks", pull_request_url, "--json", "name,bucket"],
        repo_root,
        "cannot read GitHub checks",
    )
    try:
        checks = json.loads(output)
    except json.JSONDecodeError as error:
        raise PublishError("cannot parse GitHub checks") from error
    validate_checks = [check for check in checks if check.get("name") == "validate"]
    if not validate_checks:
        raise PublishError("required validate check is missing; pull request remains open")
    if not checks or any(check.get("bucket") != "pass" for check in checks):
        raise PublishError("not all GitHub checks passed; pull request remains open")


def _wait_for_pages(
    repo_root: Path,
    repository: str,
    merge_commit: str,
    runner: CommandRunner,
    sleeper: Callable[[float], None],
) -> None:
    for _ in range(60):
        output = _run_checked(
            runner,
            ["gh", "api", f"repos/{repository}/pages/builds/latest"],
            repo_root,
            "cannot read GitHub Pages build",
        )
        try:
            build = json.loads(output)
        except json.JSONDecodeError as error:
            raise PublishError("cannot parse GitHub Pages build") from error
        status = build.get("status")
        if status == "errored" and build.get("commit") == merge_commit:
            message = (build.get("error") or {}).get("message") or "unknown Pages error"
            raise PublishError(f"GitHub Pages build failed: {message}")
        if status == "built" and build.get("commit") == merge_commit:
            return
        sleeper(5)
    raise PublishError("GitHub Pages did not deploy the merge commit within five minutes")


def _public_urls(base_url: str, manifest: Manifest) -> tuple[str, ...]:
    base = base_url.rstrip("/") + "/"
    urls = [base, urljoin(base, "artefacts/")]
    urls.extend(
        urljoin(base, "artefacts/" + public_href(entry.destination))
        for entry in manifest.entries
    )
    return tuple(dict.fromkeys(urls))


def _verify_public_urls(
    repo_root: Path,
    urls: tuple[str, ...],
    runner: CommandRunner,
) -> None:
    for url in urls:
        code = _run_checked(
            runner,
            [
                "curl",
                "--silent",
                "--show-error",
                "--location",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                url,
            ],
            repo_root,
            f"cannot request public URL {url}",
        ).strip()
        if code != "200":
            raise PublishError(f"public URL returned HTTP {code}: {url}")


def publish(
    repo_root: Path,
    source_root: Path,
    runner: CommandRunner = subprocess_runner,
    confirm: Callable[[str], str] = input,
    now: Callable[[], datetime] = datetime.now,
    sleeper: Callable[[float], None] = time.sleep,
) -> PublishResult | None:
    repo_root = repo_root.resolve()
    source_root = source_root.expanduser().resolve()
    _publish_preflight(repo_root, runner)
    manifest_path = repo_root / "artefacts" / "manifest.json"
    artefacts_root = repo_root / "artefacts"
    plan = create_sync_plan(
        manifest_path,
        source_root,
        artefacts_root,
        _head_manifest_with_runner(repo_root, runner),
    )
    print(format_plan(plan))
    if not plan.changes:
        print("No artefact changes to publish.")
        return None
    if confirm("Apply these changes and publish them? Type yes to continue: ") != "yes":
        print("Cancelled.")
        return None

    branch = f"agent/sync-artefacts-{now().strftime('%Y%m%d-%H%M%S')}"
    _run_checked(runner, ["git", "switch", "-c", branch], repo_root, "cannot create branch")
    apply_plan(plan, manifest_path, artefacts_root)
    _run_checked(
        runner,
        [sys.executable, "-B", "-m", "unittest", "tests/test_artefacts.py", "-v"],
        repo_root,
        "local unit tests failed",
    )
    _run_checked(
        runner,
        [sys.executable, "scripts/artefacts.py", "validate", "--base-ref", "origin/main"],
        repo_root,
        "local artefact validation failed",
    )

    # Stage the directory rather than the planned paths: an orphan deletion may have
    # removed an untracked file, whose path matches nothing and aborts `git add`.
    # `validate` has just proved `artefacts/` holds exactly the expected set, and
    # `.DS_Store` is ignored, so the directory pathspec stages only planned changes.
    _run_checked(
        runner,
        ["git", "add", "--all", "--", "artefacts"],
        repo_root,
        "cannot stage artefact changes",
    )
    _run_checked(
        runner,
        ["git", "commit", "-m", "chore: sync artefacts"],
        repo_root,
        "cannot commit artefact changes",
    )
    _run_checked(
        runner,
        ["git", "push", "-u", "origin", branch],
        repo_root,
        "cannot push artefact branch",
    )

    body = (
        "## Summary\n\n"
        "Synchronize the public artefact tree from the approved local source.\n\n"
        "## Preview\n\n```text\n"
        + format_plan(plan)
        + "\n```\n\n"
        "## Privacy boundary\n\n"
        "Only manifest-listed HTML, PNG, JPEG, JPG, and ICO files are published. "
        "Excluded document types and local metadata remain private.\n\n"
        "## Verification\n\n"
        "Local unit tests and repository validation passed before push.\n"
    )
    body_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="artefact-pr-", suffix=".md", delete=False
        ) as body_file:
            body_file.write(body)
            body_path = body_file.name
        pull_request_url = _run_checked(
            runner,
            [
                "gh",
                "pr",
                "create",
                "--base",
                "main",
                "--head",
                branch,
                "--title",
                "Sync published artefacts",
                "--body-file",
                body_path,
            ],
            repo_root,
            "cannot create pull request",
        ).strip()
    finally:
        if body_path and os.path.exists(body_path):
            os.unlink(body_path)
    if not pull_request_url:
        raise PublishError("GitHub did not return a pull request URL")

    _require_successful_checks(repo_root, pull_request_url, runner, sleeper)
    _run_checked(
        runner,
        ["gh", "pr", "merge", pull_request_url, "--merge"],
        repo_root,
        "cannot merge pull request",
    )
    pr_output = _run_checked(
        runner,
        ["gh", "pr", "view", pull_request_url, "--json", "state,mergeCommit,url"],
        repo_root,
        "cannot read merged pull request",
    )
    try:
        pr = json.loads(pr_output)
        merge_commit = pr["mergeCommit"]["oid"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise PublishError("cannot parse merged pull request") from error
    if pr.get("state") != "MERGED" or not merge_commit:
        raise PublishError("pull request was not merged")

    repository_output = _run_checked(
        runner,
        ["gh", "repo", "view", "--json", "nameWithOwner"],
        repo_root,
        "cannot identify GitHub repository",
    )
    try:
        repository = json.loads(repository_output)["nameWithOwner"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise PublishError("cannot parse GitHub repository") from error
    _wait_for_pages(repo_root, repository, merge_commit, runner, sleeper)
    pages_output = _run_checked(
        runner,
        ["gh", "api", f"repos/{repository}/pages"],
        repo_root,
        "cannot read GitHub Pages configuration",
    )
    try:
        base_url = json.loads(pages_output)["html_url"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise PublishError("cannot parse GitHub Pages URL") from error
    urls = _public_urls(base_url, plan.next_manifest)
    _verify_public_urls(repo_root, urls, runner)
    return PublishResult(
        pull_request_url=pull_request_url,
        merge_commit=merge_commit,
        catalogue_url=urljoin(base_url.rstrip("/") + "/", "artefacts/"),
        verified_url_count=len(urls),
        excluded_suffixes=plan.excluded_suffixes,
    )


def confirm_and_apply(
    plan: SyncPlan,
    manifest_path: Path,
    artefacts_root: Path,
    confirm,
) -> bool:
    answer = confirm("Apply these changes? Type yes to continue: ")
    if answer != "yes":
        return False
    apply_plan(plan, manifest_path, artefacts_root)
    return True


def handle_unlisted_sources(
    error: UnlistedSourceError,
    manifest_path: Path,
    source_root: Path,
    write: bool,
    confirm: Callable[[str], str],
) -> int:
    """Print derived manifest additions and, for apply and publish, write them."""
    proposal = propose_manifest_additions(error.manifest, error.unlisted, source_root)
    print(format_proposal(proposal))
    if not write:
        print("Run apply or publish to write these manifest additions.")
        return 3
    if confirm("Write these manifest additions? Type yes to continue: ") != "yes":
        print("Cancelled.")
        return 2
    payload = manifest_to_json(merge_manifest_proposal(error.manifest, proposal))
    manifest_from_bytes(payload, "proposed")
    _atomic_write(manifest_path, payload)
    print(
        "Wrote artefacts/manifest.json. Review the derived titles and descriptions, "
        "then run the command again to publish."
    )
    return 3


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_source_root() -> Path:
    return Path.home() / "Downloads" / "Artefacts"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronize published artefacts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "apply", "publish"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--repo", type=Path, default=default_repo_root())
        subparser.add_argument("--source", type=Path, default=default_source_root())
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--repo", type=Path, default=default_repo_root())
    validate_parser.add_argument("--base-ref")
    return parser


def main(argv: list[str] | None = None, input_fn=input) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo.resolve()
    artefacts_root = repo_root / "artefacts"
    manifest_path = artefacts_root / "manifest.json"
    try:
        if args.command == "validate":
            report = validate_repository(repo_root, args.base_ref)
            print(
                f"Validated {report.entry_count} manifest entries and "
                f"{report.local_link_count} local links."
            )
            if args.base_ref:
                print("Homepage files are unchanged.")
            return 0
        if args.command == "publish":
            result = publish(repo_root, args.source, confirm=input_fn)
            if result is None:
                return 0
            excluded = ", ".join(result.excluded_suffixes) or "none"
            print(f"Pull request: {result.pull_request_url}")
            print(f"Merge commit: {result.merge_commit}")
            print(f"Catalogue: {result.catalogue_url}")
            print(f"Verified URLs: {result.verified_url_count}")
            print(f"Excluded source types: {excluded}")
            return 0
        plan = create_sync_plan(
            manifest_path,
            args.source.expanduser(),
            artefacts_root,
            read_head_manifest(repo_root),
        )
        print(format_plan(plan))
        if args.command == "plan":
            return 0
        if not confirm_and_apply(plan, manifest_path, artefacts_root, input_fn):
            print("Cancelled.")
            return 2
        return 0
    except UnlistedSourceError as error:
        return handle_unlisted_sources(
            error,
            manifest_path,
            args.source.expanduser(),
            args.command in {"apply", "publish"},
            input_fn,
        )
    except ArtefactError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
