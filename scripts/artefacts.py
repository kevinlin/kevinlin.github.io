#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime
import difflib
import fnmatch
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import string
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlsplit


# Sources that become a generated page rather than a byte copy. Both publish to a
# directory index.html so the public URL carries no file extension.
DIRECTORY_INDEX_EXTENSIONS = frozenset({".html", ".md"})
APPROVED_EXTENSIONS = frozenset(
    {".html", ".md", ".png", ".jpeg", ".jpg", ".ico", ".pdf", ".webp", ".gif", ".svg"}
)
PUBLIC_COMPONENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+)?$")
PROTECTED_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CDNJS_HOST = "cdnjs.cloudflare.com"
# Editor and Finder droppings. Present in both trees, published in neither.
IGNORED_METADATA_NAMES = frozenset({".DS_Store", "Thumbs.db"})
MANIFEST_NAME = "manifest.json"
TEMPLATE_NAME = "page-template.html"
CATALOGUE_NAME = "index.html"
# Files under artefacts/ that steer the sync rather than being published by it. They
# are neither orphans nor entry destinations, and the template is not link-checked.
CONTROL_FILES = frozenset({MANIFEST_NAME, TEMPLATE_NAME, CATALOGUE_NAME})
# Orphans are warned about, never removed: an unmanaged file may be a hand-written
# page or a redirect that nothing in the manifest is meant to explain.
DELETION_KINDS = frozenset({"delete"})
WRITE_KINDS = frozenset({"add", "update"})
HOMEPAGE_FILES = ("index.html", "styles.css", "script.js")
LARGE_FILE_BYTES = 10 * 1024 * 1024
# The 3D showcase reads a generated texture atlas built from the published images.
ATLAS_SCRIPT = "scripts/build_showcase_atlas.py"
ATLAS_OUTPUT = PurePosixPath("showcase/atlas.js")


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


def _resolve_within(
    root: Path, candidate: Path, error: type[ArtefactError], message: str
) -> Path:
    """Resolve `candidate` and confirm it stays under the already-resolved `root`.

    The containment check is the boundary that keeps a symlinked or `..`-bearing
    path from reading or writing outside the tree it belongs to, so all four
    callers share one implementation rather than four copies that can drift.
    `resolve` is non-strict, so a path that does not exist yet — a destination
    about to be written, a link target being checked for breakage — resolves as
    far as it can and is still compared.
    """
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise error(message)
    return resolved


@dataclass(frozen=True)
class Site:
    """Everything about the published site that is not a file: the manifest's `site` block.

    Held in the manifest rather than in the script so the same code publishes any
    Pages repository, and so a favicon or a catalogue mode is reviewable in a diff.
    """

    base_url: str
    favicon: str
    catalogue_mode: str
    catalogue_page: PurePosixPath | None


@dataclass(frozen=True)
class Collection:
    id: str
    title: str
    description: str | None
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
    description: str | None = None
    # ISO date the catalogue sorts and stamps cards by. Written from the source's
    # modification time when the entry is first published and again when a sync
    # republishes it, and otherwise left alone: a re-download must not silently
    # reorder the catalogue, and a hand-set date has to survive a run that changes
    # nothing.
    date: str | None = None


@dataclass(frozen=True)
class Manifest:
    version: int
    site: Site
    protected_files: tuple[PurePosixPath, ...]
    collections: tuple[Collection, ...]
    entries: tuple[Entry, ...]
    # Source paths the scan subtracts before reconciliation. A trailing "/" makes a
    # rule cover a subtree, and a rule containing *, ? or [ is an fnmatch glob.
    # Kept as written rather than as PurePosixPath, because PurePosixPath discards
    # the trailing separator the forms are told apart by.
    ignored_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceInventory:
    approved: tuple[PurePosixPath, ...]
    excluded: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ManifestProposal:
    collections: tuple[Collection, ...]
    entries: tuple[Entry, ...]
    warnings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceReconciliation:
    next_manifest: Manifest
    missing_entries: tuple[Entry, ...]


@dataclass(frozen=True)
class Change:
    kind: str
    destination: PurePosixPath
    source: PurePosixPath | None = None
    size: int | None = None
    url: str = ""
    diff: str | None = None


@dataclass(frozen=True)
class Note:
    """Something the run wants read but will not stop for."""

    kind: str
    where: str
    detail: str


@dataclass(frozen=True)
class SyncPlan:
    manifest: Manifest
    next_manifest: Manifest
    desired_files: dict[PurePosixPath, bytes]
    changes: tuple[Change, ...]
    unchanged: tuple[PurePosixPath, ...]
    # The closed allowlist's other two outcomes, per suffix and per ignore rule.
    excluded: tuple[tuple[str, int], ...] = ()
    ignored: tuple[tuple[str, int], ...] = ()
    notes: tuple[Note, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    entry_count: int
    local_link_count: int
    ignored_metadata_count: int
    notes: tuple[Note, ...] = ()


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
    excluded: tuple[tuple[str, int], ...]


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


SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
FETCHES_NOTHING = ("data:", "mailto:", "tel:", "#")


class _LoadParser(HTMLParser):
    """Line and URL of every external resource a page fetches while rendering."""

    def __init__(self) -> None:
        super().__init__()
        self.loads: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is None:
                continue
            if name != "src" and not (name == "href" and tag.lower() == "link"):
                continue
            url = value.strip()
            if not (url.startswith("//") or SCHEME.match(url)):
                continue
            if url.lower().startswith(FETCHES_NOTHING):
                continue
            self.loads.append((self.getpos()[0], url))


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


def _optional_string(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ManifestError(f"{name} must be a non-empty string when present")
    return value


DEFAULT_FAVICON = '<link rel="icon" href="data:,">'


def site_from_dict(payload: Any) -> Site:
    if not isinstance(payload, dict):
        raise ManifestError("site must be an object")
    base_url = payload.get("base_url")
    if not isinstance(base_url, str) or not base_url.endswith("/"):
        raise ManifestError("site.base_url must be a URL ending in '/'")
    catalogue = payload.get("catalogue") or {"mode": "standalone"}
    if not isinstance(catalogue, dict):
        raise ManifestError("site.catalogue must be an object")
    mode = catalogue.get("mode", "standalone")
    if mode not in ("standalone", "inject"):
        raise ManifestError(
            f"site.catalogue.mode must be standalone or inject, got {mode!r}"
        )
    page = catalogue.get("page")
    if mode == "inject" and not page:
        raise ManifestError("site.catalogue.mode 'inject' needs a 'page'")
    page_path = _safe_relative_path(page, "site.catalogue.page") if page else None
    favicon = payload.get("favicon", DEFAULT_FAVICON)
    if not isinstance(favicon, str) or not favicon:
        raise ManifestError("site.favicon must be a non-empty string")
    return Site(
        base_url=base_url,
        favicon=favicon,
        catalogue_mode=mode,
        catalogue_page=page_path,
    )


def site_to_dict(site: Site) -> dict[str, Any]:
    catalogue: dict[str, Any] = {"mode": site.catalogue_mode}
    if site.catalogue_page is not None:
        catalogue["page"] = site.catalogue_page.as_posix()
    return {
        "base_url": site.base_url,
        "favicon": site.favicon,
        "catalogue": catalogue,
    }


def _entry_date(payload: dict[str, Any]) -> str | None:
    stamp = payload.get("date")
    if stamp is None:
        return None
    if not isinstance(stamp, str) or not ISO_DATE.match(stamp):
        raise ManifestError(f"entry {payload.get('id')!r}: date must be YYYY-MM-DD")
    try:
        date.fromisoformat(stamp)
    except ValueError as error:
        raise ManifestError(f"entry {payload.get('id')!r}: {error}") from error
    return stamp


def manifest_from_dict(payload: dict[str, Any]) -> Manifest:
    if not isinstance(payload, dict):
        raise ManifestError("manifest must be a JSON object")
    try:
        site_payload = payload["site"]
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
            description=_optional_string(item, "description"),
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
                description=_optional_string(item, "description"),
                date=_entry_date(item),
            )
        )

    ignored_payload = payload.get("ignored_sources", [])
    if not isinstance(ignored_payload, list):
        raise ManifestError("ignored_sources must be an array")
    ignored_sources = tuple(
        _safe_ignore_rule(value) for value in ignored_payload
    )

    return Manifest(
        version=payload.get("version"),
        site=site_from_dict(site_payload),
        protected_files=protected_files,
        collections=collections,
        entries=tuple(entries),
        ignored_sources=ignored_sources,
    )


def _safe_ignore_rule(value: Any) -> str:
    """One ignore rule, validated as a relative path with its trailing "/" kept."""
    if not isinstance(value, str):
        raise ManifestError("ignored source must be a safe relative path")
    _safe_relative_path(value.rstrip("/"), "ignored source")
    return value


def _require_unique(values: list[Any], message: str) -> None:
    if len(values) != len(set(values)):
        raise ManifestError(message)


def _validate_path_components(
    path: PurePosixPath, pattern: re.Pattern[str], message: str
) -> None:
    for component in path.parts:
        if not pattern.fullmatch(component):
            raise ManifestError(
                f"{message}: {path.as_posix()} (component {component!r})"
            )


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
    _require_unique(list(manifest.ignored_sources), "duplicate ignored source")

    # An entry says publish this and an ignore rule says never look at it. Resolving
    # the contradiction either way would hide an edit the user made by hand.
    for entry in manifest.entries:
        if _is_ignored(entry.source, manifest.ignored_sources):
            raise ManifestError(
                f"ignored source is also an entry source: {entry.source.as_posix()}"
            )

    collection_ids = {collection.id for collection in manifest.collections}
    reserved_destinations = {PurePosixPath(name) for name in CONTROL_FILES}
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
        _validate_path_components(
            entry.destination, PUBLIC_COMPONENT, "destination must be lowercase kebab-case"
        )
        if source_suffix in DIRECTORY_INDEX_EXTENSIONS:
            if entry.destination.name != "index.html":
                raise ManifestError(
                    f"generated destination for entry {entry.id} must end in index.html"
                )
        elif destination_suffix != source_suffix:
            raise ManifestError(
                f"image destination for entry {entry.id} must keep source extension"
            )

    for path in manifest.protected_files:
        _validate_path_components(
            path, PROTECTED_COMPONENT, "protected file must use a lowercase safe path"
        )


def _renumber_colliding_orders(
    items: tuple[Any, ...], group_of: Callable[[Any], Any]
) -> tuple[Any, ...]:
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
        content = path.read_bytes()
    except OSError as error:
        raise ManifestError(f"cannot read manifest: {error}") from error
    return manifest_from_bytes(content, "manifest")


def scan_source(source_root: Path, repo_root: Path) -> SourceInventory:
    """Inventory approved files without following symlinks or entering the destination repo.

    A symlink is skipped rather than fatal: the source folder is a working
    directory a person keeps shortcuts in, and one shortcut must not stop every
    command. The destination repository is pruned by resolved path, so a clone
    sitting inside the source folder under any name stays out of the inventory.
    Every non-approved file is counted by suffix instead of being dropped
    silently — the closed allowlist's other outcome is something `plan` reports.
    """
    if not source_root.is_dir():
        raise InventoryError(f"source directory does not exist: {source_root}")
    resolved_root = source_root.resolve()
    pruned = repo_root.resolve()
    approved: list[PurePosixPath] = []
    excluded: dict[str, int] = {}

    for current_root, directory_names, file_names in os.walk(resolved_root):
        current_path = Path(current_root)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not (current_path / name).is_symlink()
            and (current_path / name).resolve() != pruned
        )
        for name in sorted(file_names):
            candidate = current_path / name
            if candidate.is_symlink() or name in IGNORED_METADATA_NAMES:
                continue
            suffix = candidate.suffix.lower()
            if suffix not in APPROVED_EXTENSIONS:
                label = suffix or "(no suffix)"
                excluded[label] = excluded.get(label, 0) + 1
                continue
            approved.append(
                PurePosixPath(candidate.relative_to(resolved_root).as_posix())
            )

    return SourceInventory(
        approved=tuple(sorted(approved, key=str)),
        excluded=tuple(sorted(excluded.items())),
    )


def _is_ignored(source: PurePosixPath, rules: tuple[str, ...]) -> bool:
    """Whether one source matches any ignore rule.

    Three forms, because exact-string-or-literal-prefix matching quietly published
    the files it was meant to hide: a rule ending in "/" covers a subtree, and a
    bare directory name covers that directory at any depth; a rule containing *, ?
    or [ is an fnmatch glob tried against both the full path and the file name;
    anything else is an exact path.
    """
    text = source.as_posix()
    for rule in rules:
        if rule.endswith("/"):
            directory = rule.rstrip("/")
            if ("/" not in directory and directory in source.parts[:-1]) or text.startswith(rule):
                return True
        if any(character in rule for character in "*?[") and (
            fnmatch.fnmatchcase(text, rule) or fnmatch.fnmatchcase(source.name, rule)
        ):
            return True
        if text == rule:
            return True
    return False


def apply_source_ignores(
    inventory: SourceInventory, rules: tuple[str, ...]
) -> tuple[SourceInventory, tuple[tuple[str, int], ...]]:
    """The inventory without the ignored sources, and each rule's match count.

    Applied between the scan and the reconciliation so `scan_source` stays
    manifest-unaware and every rule downstream sees an inventory the ignored files
    were never in. The counts are returned rather than logged because a silently
    skipped file is the one way this list can lose work.
    """
    approved = tuple(
        source for source in inventory.approved if not _is_ignored(source, rules)
    )
    counts = tuple(
        (
            rule,
            sum(1 for source in inventory.approved if _is_ignored(source, (rule,))),
        )
        for rule in rules
    )
    return replace(inventory, approved=approved), counts


_SVG_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("script element", re.compile(r"<\s*script\b", re.IGNORECASE)),
    ("foreignObject element", re.compile(r"<\s*foreignObject\b", re.IGNORECASE)),
    ("event handler attribute", re.compile(r"\bon[a-z]+\s*=", re.IGNORECASE)),
    (
        "external reference",
        re.compile(
            r"\b(?:xlink:)?href\s*=\s*[\"']\s*(?:[a-z][a-z0-9+.-]*:)?//", re.IGNORECASE
        ),
    ),
    ("javascript: url", re.compile(r"[\"'(]\s*javascript\s*:", re.IGNORECASE)),
    (
        "data: url",
        re.compile(r"\b(?:xlink:)?href\s*=\s*[\"']\s*data\s*:", re.IGNORECASE),
    ),
    (
        "external css url()",
        re.compile(r"url\(\s*[\"']?\s*(?:[a-z][a-z0-9+.-]*:)?//", re.IGNORECASE),
    ),
    (
        "external entity declaration",
        re.compile(r"<!ENTITY\b[^>]*\b(?:SYSTEM|PUBLIC)\b", re.IGNORECASE),
    ),
)


def validate_svg(data: bytes, label: str) -> None:
    """Refuse SVG scripts, handlers, and external references without rewriting bytes.

    Reject and name the line; never sanitise. A standard-library sanitiser that
    misses `foreignObject`, `xlink:href` or a CSS `url()` is worse than none,
    because the user then trusts the file it passed.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValidationError(f"{label}: not valid UTF-8 ({error})") from error

    problems: list[tuple[int, str]] = []
    for reason, pattern in _SVG_RULES:
        for match in pattern.finditer(text):
            number = text.count("\n", 0, match.start()) + 1
            problems.append((number, f"{label}:{number}: {reason} ({match.group(0).strip()!r})"))
    if problems:
        problems.sort()
        raise ValidationError(
            "\n".join(message for _, message in problems)
            + "\nSVG must not contain scripts, event handlers, or external references."
        )


SLUG_SEPARATOR = re.compile(r"[^a-z0-9]+")
LEADING_NUMBER = re.compile(r"^\d+[-_ ]+")
WORD_SEPARATOR = re.compile(r"[-_]+")
REPEATED_SPACE = re.compile(r"\s+")
TRAILING_SPACE = re.compile(r"[ \t]+(?=\r?$)", re.MULTILINE)

MARKDOWN_BLOCK_START = '<script type="text/markdown" id="markdown-source">\n'
MARKDOWN_BLOCK_END = "</script>"

# Raw text in a <script> element ends at a literal `</script`, and `<!--` opens the
# double-escaped parse state where the terminator rules change. Both are escaped by
# inserting one backslash after the `<`. The pattern also matches an already-escaped
# marker, so escaping adds a backslash and unescaping removes exactly one: the round
# trip is lossless even for a source that contains the escaped form verbatim.
MARKDOWN_MARKER = re.compile(r"<(\\*)(/script|!--)", re.IGNORECASE)


def escape_markdown_block(text: str) -> str:
    return MARKDOWN_MARKER.sub(
        lambda match: "<" + "\\" * (len(match.group(1)) + 1) + match.group(2), text
    )


def unescape_markdown_block(text: str) -> str:
    return MARKDOWN_MARKER.sub(
        lambda match: "<" + "\\" * max(len(match.group(1)) - 1, 0) + match.group(2), text
    )


def extract_markdown(document: str) -> str | None:
    """The Markdown embedded in a generated page, or None if there is no block.

    Exact inverse of the embedding in `render_markdown_page`, and the basis of the
    diff preview. A published page that predates this scheme, or one edited by
    hand, has no block and yields None rather than an error.
    """
    start = document.find(MARKDOWN_BLOCK_START)
    if start < 0:
        return None
    start += len(MARKDOWN_BLOCK_START)
    end = document.find(MARKDOWN_BLOCK_END, start)
    if end < 0:
        return None
    return unescape_markdown_block(document[start:end])


MARKDOWN_VENDOR_NAME = "marked.min.js"
EXISTING_ICON_LINK = re.compile(r"""<link\b[^>]*\brel=["']?[^"'>]*\bicon\b""", re.IGNORECASE)
HEAD_OPEN = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
DOCTYPE = re.compile(r"^\s*<!doctype[^>]*>", re.IGNORECASE)


def load_template(artefacts_root: Path) -> string.Template:
    """The Markdown page template, read from the tree it publishes into.

    A real file rather than a string in this script: its CSS needs no brace
    escaping, it previews in a browser, and a restyle is reviewable as a diff of
    the page it changes. `string.Template` because `$title` does not collide with
    the braces the template's CSS and JavaScript are full of.
    """
    path = artefacts_root / TEMPLATE_NAME
    try:
        return string.Template(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise TransformationError(f"cannot read page template {path}: {error}") from error


def markdown_vendor_path(manifest: Manifest) -> PurePosixPath:
    for path in manifest.protected_files:
        if path.name == MARKDOWN_VENDOR_NAME:
            return path
    raise TransformationError(
        f"{MARKDOWN_VENDOR_NAME} must be listed in protected_files to publish Markdown"
    )


def normalise_source_text(source_bytes: bytes, label: str) -> str:
    """Decode UTF-8, normalise line endings, and guarantee a final newline.

    Line endings are normalised because git with `core.autocrlf=input` — a common
    default — stores LF for a CRLF working-tree file. A page that kept its CRs is
    not the page that gets committed, so a fresh clone reports the same entry
    changed on every run, forever.
    """
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TransformationError(f"{label}: not UTF-8 ({error})") from error
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def render_markdown_page(
    entry: Entry,
    source_bytes: bytes,
    vendor_path: PurePosixPath,
    site: Site,
    template: string.Template,
) -> bytes:
    """One self-contained page carrying the Markdown verbatim.

    The Markdown is embedded rather than converted because this script is
    standard-library only. Its text is preserved exactly after line-ending
    normalisation: the trailing-space stripping `transform_html` applies would turn
    a Markdown hard line break into a soft one, and both `apply`'s round-trip check
    and the diff preview depend on the embed-extract round trip being lossless.
    """
    text = normalise_source_text(source_bytes, entry.source.as_posix())
    # `$prefix` is the `../` climb and `$vendor` the vendor path alone, so a
    # template composes `src="$prefix$vendor"`. Baking the climb into `$vendor`
    # would double it for any template that spells both.
    prefix = "../" * len(entry.destination.parent.parts)
    document = template.substitute(
        title=html.escape(entry.title),
        favicon=site.favicon,
        prefix=prefix,
        vendor=vendor_path.as_posix(),
        block_start=MARKDOWN_BLOCK_START,
        markdown=escape_markdown_block(text),
        block_end=MARKDOWN_BLOCK_END,
    )
    return document.encode("utf-8")


MARKDOWN_DIFF_LINE_LIMIT = 40


def markdown_diff(
    published: bytes | None,
    rendered: bytes,
    limit: int = MARKDOWN_DIFF_LINE_LIMIT,
) -> str:
    """Unified diff between the published page's Markdown and the rendered page's.

    Both sides are read back out of a rendered page, so the diff is computed over
    exactly the text the byte comparison classified as changed and cannot disagree
    with it. Truncation is stated rather than silent: a cut-off diff that looked
    complete would be worse than no diff.
    """
    if published is None:
        return ""
    try:
        document = published.decode("utf-8")
        current_document = rendered.decode("utf-8")
    except UnicodeDecodeError:
        return "diff unavailable: page is not UTF-8"
    previous = extract_markdown(document)
    current = extract_markdown(current_document)
    if previous is None or current is None:
        return "diff unavailable: page has no embedded Markdown"
    lines = list(
        difflib.unified_diff(
            previous.splitlines(),
            current.splitlines(),
            fromfile="published",
            tofile="source",
            lineterm="",
        )
    )
    if not lines:
        return ""
    if len(lines) > limit:
        remaining = len(lines) - limit
        lines = lines[:limit] + [f"… truncated, {remaining} more lines"]
    return "\n".join(lines)


def _slug(value: str) -> str:
    slug = SLUG_SEPARATOR.sub("-", value.lower()).strip("-")
    if not slug:
        raise InventoryError(f"cannot normalize path component: {value}")
    return slug


def suggest_destination(source: PurePosixPath) -> PurePosixPath:
    parent_parts = tuple(_slug(part) for part in source.parent.parts if part != ".")
    stem = _slug(source.stem)
    suffix = source.suffix.lower()
    if suffix in DIRECTORY_INDEX_EXTENSIONS:
        return PurePosixPath(*parent_parts, stem, "index.html")
    return PurePosixPath(*parent_parts, f"{stem}{suffix}")


PRESENTATION_SECTION = "Presentations and analysis"
IMAGE_SECTION = "Image collections"
# Root-level sources have no directory to name their collection after. Naming it for
# whichever file happens to sort first is arbitrary, and the arbitrary run is the
# first one a new source folder sees.
ROOT_COLLECTION_LABEL = "General"
CDNJS_REFERENCE = re.compile(rf"https://{re.escape(CDNJS_HOST)}/[^\s\"'<>)]+")


def _normalize_words(stem: str) -> str:
    text = LEADING_NUMBER.sub("", stem)
    text = WORD_SEPARATOR.sub(" ", text)
    text = REPEATED_SPACE.sub(" ", text).strip()
    if not text:
        raise InventoryError(f"cannot derive a title from: {stem}")
    return text


def _derive_title(stem: str) -> str:
    text = _normalize_words(stem)
    return text[0].upper() + text[1:]


MARKDOWN_HEADING = re.compile(r"^#[ \t]+(\S.*?)[ \t]*#*[ \t]*$", re.MULTILINE)


def markdown_title(source_path: Path) -> str | None:
    """The document's first level-one heading, or None when it has none.

    The stem rule normalizes separators and sentence-cases, which loses whatever
    casing and punctuation the author chose. The heading they already wrote is the
    better starting point, and the result is still a proposal the user edits before
    publishing.
    """
    try:
        text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = MARKDOWN_HEADING.search(text)
    return match.group(1) if match else None


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


def _max_orders(items, group_of: Callable[[Any], str]) -> dict[str, int]:
    """Highest declared order per group, used to continue existing numbering."""
    result: dict[str, int] = {}
    for item in items:
        key = group_of(item)
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
    would not always be found. The replacements are applied here exactly as
    `transform_html` will apply them, so a reference this function could not map is
    reported on the proposal rather than discovered in the published page.
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
    return replacements, CDNJS_REFERENCE.search(text) is not None


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
    order_in_section = _max_orders(
        manifest.collections, lambda collection: collection.section
    )
    order_in_collection = _max_orders(manifest.entries, lambda entry: entry.collection)
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
        if collection_id is None and group and _slug(group) in collection_ids:
            # The group's collection exists but currently holds no entry, as after
            # a source rename drops the only one. Reuse it rather than inventing a
            # second collection beside it under a suffixed id.
            collection_id = _slug(group)
        if collection_id is None:
            label = group or ROOT_COLLECTION_LABEL
            collection_id = _unique_id(_slug(label), collection_ids)
            collection_ids.add(collection_id)
            # Classified on the source extensions that produce a directory
            # index.html, because that is the destination suffix
            # `_sections_by_media` reads the collection back by. Matching only
            # ".html" here would file a Markdown-only collection under images and
            # move it to presentations on the next proposal.
            is_presentation = any(
                source.suffix.lower() in DIRECTORY_INDEX_EXTENSIONS
                for source in sources
            )
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
                    # No description. A placeholder string would publish itself to
                    # the catalogue on the next apply; an absent one prints nothing,
                    # so the card reads correctly until someone writes one.
                    description=None,
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
            source_path = source_root / source.as_posix()
            title = _derive_title(source.stem)
            suffix = source.suffix.lower()
            if suffix == ".html":
                replacements, unmapped = _vendor_replacements(
                    vendor_by_name, source_path, destination
                )
                if unmapped:
                    warnings[entry_id] = (
                        "an unmapped cdnjs reference remains; the published page "
                        "will load it from cdnjs unless you vendor it into "
                        "protected_files"
                    )
            else:
                # Markdown entries declare no replacements: the generated page owns
                # its references, and a cdnjs URL in the prose is content.
                replacements = {}
                if suffix == ".md":
                    title = markdown_title(source_path) or title
            entries.append(
                Entry(
                    id=entry_id,
                    source=source,
                    destination=destination,
                    title=title,
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
        if collection.description is not None:
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


def drop_entries_without_source(
    manifest: Manifest, approved: set[PurePosixPath]
) -> tuple[Manifest, tuple[Entry, ...]]:
    """Manifest without the entries whose source file is gone, and those entries."""
    missing = tuple(entry for entry in manifest.entries if entry.source not in approved)
    kept = replace(
        manifest,
        entries=tuple(entry for entry in manifest.entries if entry.source in approved),
    )
    return kept, missing


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
    next_manifest, missing = drop_entries_without_source(manifest, approved)
    return SourceReconciliation(next_manifest=next_manifest, missing_entries=missing)


def ensure_favicon(text: str, favicon: str) -> str:
    """Give a source page the site favicon when it declares none of its own."""
    if EXISTING_ICON_LINK.search(text):
        return text
    head = HEAD_OPEN.search(text)
    if head is not None:
        return f"{text[: head.end()]}\n    {favicon}{text[head.end() :]}"
    # Fragment with no explicit <head>: the parser hoists a leading <link> into the
    # head it synthesises, so prepending works — but never before the doctype.
    doctype = DOCTYPE.match(text)
    prefix = f"{text[: doctype.end()]}\n" if doctype else ""
    rest = text[doctype.end() :] if doctype else text
    return f"{prefix}{favicon}\n{rest.lstrip()}"


def external_references(text: str) -> tuple[tuple[int, str], ...]:
    """Every line and URL in a page that will be fetched from another host at runtime.

    Generalised from a cdnjs-only ban: any external host is the same fragility —
    the page stops rendering the day that host does — and the ban only ever named
    one of them. Reported as a warning per reference rather than a hard refusal,
    because a font or an analytics endpoint is a decision, not a defect.

    Only what the page *loads* counts: any `src`, and `href` on `<link>`. An `<a href>`
    to another site is a citation, which is the point of a research artefact and not a
    dependency — counting those buried the real findings under one warning per footnote.
    `data:`, `mailto:`, `tel:` and fragments fetch nothing.
    """
    parser = _LoadParser()
    parser.feed(text)
    parser.close()
    return tuple(parser.loads)


def transform_html(entry: Entry, source_bytes: bytes, site: Site) -> bytes:
    text = normalise_source_text(source_bytes, entry.source.as_posix())
    for old, new in entry.replacements.items():
        parts = text.split(old)
        if len(parts) == 1:
            raise TransformationError(
                f"expected replacement not found for {entry.id}: {old}"
            )
        text = new.join(parts)
    text = TRAILING_SPACE.sub("", text)
    text = ensure_favicon(text, site.favicon)
    return text.encode("utf-8")


def build_desired_files(
    manifest: Manifest, source_root: Path, template: string.Template
) -> dict[PurePosixPath, bytes]:
    resolved_root = source_root.resolve()
    desired: dict[PurePosixPath, bytes] = {}
    for entry in manifest.entries:
        source_path = source_root / entry.source.as_posix()
        if not source_path.exists():
            continue
        if source_path.is_symlink():
            raise InventoryError(f"symbolic link is not allowed: {source_path}")
        _resolve_within(
            resolved_root,
            source_path,
            InventoryError,
            f"source path escapes source directory: {source_path}",
        )
        source_bytes = source_path.read_bytes()
        suffix = entry.source.suffix.lower()
        if suffix == ".html":
            output = transform_html(entry, source_bytes, manifest.site)
        elif suffix == ".md":
            # Looked up per entry on purpose: a manifest with no Markdown must not
            # require the parser to be vendored.
            output = render_markdown_page(
                entry,
                source_bytes,
                markdown_vendor_path(manifest),
                manifest.site,
                template,
            )
        else:
            output = source_bytes
        desired[entry.destination] = output
    return desired


def public_href(destination: PurePosixPath) -> str:
    if destination.name == "index.html":
        parent = destination.parent.as_posix()
        return "" if parent == "." else parent.rstrip("/") + "/"
    return destination.as_posix()


def public_url(site: Site, destination: PurePosixPath) -> str:
    """The full URL a reader will type. Spelled out, because the plan asks about URLs."""
    return site.base_url + public_href(destination)


def stamp_dates(
    manifest: Manifest, source_root: Path, republished: set[PurePosixPath]
) -> Manifest:
    """Set `date` from the source's modification time for undated and republished entries.

    Written into the manifest rather than recomputed each run: the catalogue sorts
    cards by date, so reading the filesystem every time lets a re-download silently
    reorder the page, and a hand-corrected date would be overwritten on the next
    sync. It is refreshed only when this run actually republishes the artefact, so a
    published date names the version on the page. An entry whose source is gone keeps
    whatever it already had.
    """
    entries: list[Entry] = []
    for entry in manifest.entries:
        source_path = source_root / entry.source.as_posix()
        if entry.date is None or entry.destination in republished:
            try:
                stamp = date.fromtimestamp(source_path.stat().st_mtime).isoformat()
            except OSError:
                stamp = None
            if stamp is not None:
                entries.append(replace(entry, date=stamp))
                continue
        entries.append(entry)
    return replace(manifest, entries=tuple(entries))


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

    # ISO dates sort chronologically, so the card carries its newest entry's date and
    # the section orders its cards by that string. A collection whose entries are all
    # undated sorts as "" and falls to the bottom on its declared order.
    latest_by_collection = {
        collection_id: max(
            (entry.date for entry in entries if entry.date), default=""
        )
        for collection_id, entries in entries_by_collection.items()
    }

    lines: list[str] = []
    for (_, section_title), collections in sorted(sections.items()):
        heading_id = f"{_slug(section_title)}-heading"
        heading = html.escape(section_title)
        lines.extend(
            [
                f'        <section aria-labelledby="{heading_id}">',
                f'            <h2 id="{heading_id}">{heading}</h2>',
                '            <div class="card-grid">',
            ]
        )
        # Newest card first. Sorting on `order` first and then re-sorting on the date
        # keeps `order` as the tie-break: Python's sort is stable, and reverse=True
        # does not reverse equal elements. An undated card sorts as "", so it lands
        # last.
        cards = sorted(collections, key=lambda item: item.order)
        cards.sort(key=lambda item: latest_by_collection[item.id], reverse=True)
        for collection in cards:
            lines.extend(
                [
                    '                <article class="card">',
                    f"                    <h3>{html.escape(collection.title)}</h3>",
                ]
            )
            if collection.description is not None:
                lines.append(
                    f"                    <p>{html.escape(collection.description)}</p>"
                )
            latest = latest_by_collection[collection.id]
            if latest:
                lines.append(
                    '                    <p class="card-updated">Updated '
                    f'<time datetime="{latest}">{latest}</time></p>'
                )
            lines.append("                    <ul>")
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


def _collection_to_dict(collection: Collection) -> dict[str, Any]:
    body: dict[str, Any] = {"id": collection.id, "title": collection.title}
    if collection.description is not None:
        body["description"] = collection.description
    body.update(
        section=collection.section,
        section_order=collection.section_order,
        order=collection.order,
    )
    return body


def _entry_to_dict(entry: Entry) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": entry.id,
        "source": entry.source.as_posix(),
        "destination": entry.destination.as_posix(),
        "title": entry.title,
        "collection": entry.collection,
        "order": entry.order,
        "replacements": dict(entry.replacements),
    }
    if entry.description is not None:
        body["description"] = entry.description
    if entry.date is not None:
        body["date"] = entry.date
    return body


def manifest_to_json(manifest: Manifest) -> bytes:
    payload = {
        "version": manifest.version,
        "site": site_to_dict(manifest.site),
        "protected_files": [path.as_posix() for path in manifest.protected_files],
        "ignored_sources": list(manifest.ignored_sources),
        "collections": [
            _collection_to_dict(collection) for collection in manifest.collections
        ],
        "entries": [_entry_to_dict(entry) for entry in manifest.entries],
    }
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def manifest_from_bytes(content: bytes, description: str) -> Manifest:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read {description}: {error}") from error
    manifest = manifest_from_dict(payload)
    validate_manifest(manifest)
    return manifest


def published_manifest(content: bytes | None) -> Manifest | None:
    """The committed manifest, read leniently, for the published-URL guard alone.

    Lenient on purpose. This value only ever feeds `check_published_invariants`,
    which reads `id`, `destination` and `title`. A repository whose committed
    manifest predates the `site` block would otherwise fail the whole run on a
    field the check never touches, while returning None outright would drop the
    URL-freeze guard on exactly the run where published destinations are at stake.
    A placeholder keeps the guard alive.
    """
    if content is None:
        return None
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        payload.setdefault("site", {"base_url": "https://head.invalid/"})
    try:
        return manifest_from_dict(payload)
    except ManifestError:
        return None


def check_published_invariants(current: Manifest, head: bytes | None) -> None:
    """Refuse an edit that changes an existing entry's public URL or its title.

    A destination is frozen once published: a reader's bookmark, a link in someone
    else's page and a search result all point at the old one. A title is frozen with
    it, because the catalogue link text is how the page is found again.
    """
    published = published_manifest(head)
    if published is None:
        return
    previous = {entry.id: entry for entry in published.entries}
    problems: list[str] = []
    for entry in current.entries:
        was = previous.get(entry.id)
        if was is None:
            continue
        if entry.destination != was.destination:
            problems.append(
                f"entry {entry.id!r}: destination {was.destination.as_posix()} -> "
                f"{entry.destination.as_posix()} would break the published URL "
                f"for {was.destination.as_posix()}"
            )
        if entry.title != was.title:
            problems.append(
                f"entry {entry.id!r}: title {was.title!r} -> {entry.title!r}; "
                "an existing entry is never re-titled"
            )
    if problems:
        raise ManifestError("\n".join(problems))


def _validate_desired_tree(
    repo_root: Path,
    artefacts_root: Path,
    manifest: Manifest,
    desired_files: dict[PurePosixPath, bytes],
) -> None:
    with tempfile.TemporaryDirectory(prefix="artefact-plan-") as directory:
        planned_repo = Path(directory)
        for name in HOMEPAGE_FILES:
            source = repo_root / name
            if source.is_file():
                (planned_repo / name).write_bytes(source.read_bytes())
        planned_artefacts = planned_repo / "artefacts"
        planned_artefacts.mkdir()
        # A control file steers the sync instead of being produced by it, so it is
        # in no desired-files map and has to be carried across by hand.
        template = artefacts_root / TEMPLATE_NAME
        if template.is_file():
            (planned_artefacts / TEMPLATE_NAME).write_bytes(template.read_bytes())
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
        if path.name in IGNORED_METADATA_NAMES:
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


_SECRET_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "looks like an AWS access key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "looks like an API key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "contains a private key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "looks like a GitHub token"),
    (re.compile(r"\b[a-fA-F0-9]{40,}\b"), "contains a long hexadecimal secret shape"),
)
# Matched at word boundaries, not as a path-component prefix: the component rule let
# "Client Presentation.pdf", "Internal Notes.html" and "q1-internal-review.md" through
# silently, and those are the shapes a real source folder holds.
_PRIVATE_WORD = re.compile(
    r"(?<![a-z0-9])(?:prompts?|drafts?|internal|client)(?![a-z0-9])", re.IGNORECASE
)
# Sources worth reading for secret shapes. A binary is scanned by name only.
TEXT_SUFFIXES = frozenset({".html", ".md", ".svg"})


def external_note(where: str, url: str) -> Note:
    return Note("external", where, f"loads {url} at runtime")


def source_warnings(source: PurePosixPath, text: str | None) -> list[Note]:
    """Filename heuristics plus secret shapes for one source.

    A public seam rather than an inline loop, because these are the checks that stop
    a private file going public and they need tests of their own. `text` is None for
    a binary source, which is checked by name alone.
    """
    label = source.as_posix()
    notes: list[Note] = []
    match = _PRIVATE_WORD.search(label)
    if match is not None:
        notes.append(Note("secret", label, f'filename contains "{match.group(0).lower()}"'))
    if text is None:
        return notes
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern, detail in _SECRET_RULES:
            if pattern.search(line):
                notes.append(Note("secret", f"{label}:{number}", detail))
    return notes


def _source_notes(
    manifest: Manifest, source_root: Path, desired_files: dict[PurePosixPath, bytes]
) -> list[Note]:
    notes: list[Note] = []
    for entry in manifest.entries:
        text: str | None = None
        if entry.source.suffix.lower() in TEXT_SUFFIXES:
            try:
                text = (source_root / entry.source.as_posix()).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                text = None
        notes.extend(source_warnings(entry.source, text))
        if entry.source.suffix.lower() != ".html":
            continue
        rendered = desired_files.get(entry.destination)
        if rendered is None:
            continue
        try:
            document = rendered.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for number, url in external_references(document):
            notes.append(external_note(f"{entry.source.as_posix()}:{number}", url))
    return notes


def _catalogue_destination(manifest: Manifest) -> PurePosixPath:
    if manifest.site.catalogue_mode == "standalone":
        return PurePosixPath(CATALOGUE_NAME)
    if manifest.site.catalogue_page is None:
        raise CatalogueError("site.catalogue inject mode needs a page")
    return manifest.site.catalogue_page


def create_sync_plan(
    manifest_path: Path,
    source_root: Path,
    artefacts_root: Path,
    head_manifest: bytes | None,
) -> SyncPlan:
    repo_root = artefacts_root.parent
    declared = load_manifest(manifest_path)
    manifest = normalize_orders(declared)
    inventory, ignored_rules = apply_source_ignores(
        scan_source(source_root, repo_root), manifest.ignored_sources
    )
    reconciliation = reconcile_inventory(manifest, inventory)
    next_manifest = reconciliation.next_manifest
    check_published_invariants(next_manifest, head_manifest)

    # Everything that must stop the run, gathered before anything is rendered, so one
    # run names every problem instead of the first one.
    problems: list[str] = []
    for entry in next_manifest.entries:
        if entry.source.suffix.lower() != ".svg":
            continue
        source_path = source_root / entry.source.as_posix()
        if source_path.is_file():
            try:
                validate_svg(source_path.read_bytes(), entry.source.as_posix())
            except ValidationError as error:
                problems.append(str(error))
    for protected in next_manifest.protected_files:
        path = artefacts_root / protected.as_posix()
        if not path.is_file() or path.is_symlink():
            problems.append(f"{protected.as_posix()}: missing protected file")
    if problems:
        raise ValidationError("\n".join(problems))

    template = load_template(artefacts_root)
    desired_files = build_desired_files(next_manifest, source_root, template)
    # Dates reach only manifest.json and the catalogue, never the artefact pages, so
    # they can settle once the pages are rendered and it is clear which ones change.
    republished = {
        destination
        for destination, content in desired_files.items()
        if (artefacts_root / destination.as_posix()).is_file()
        and (artefacts_root / destination.as_posix()).read_bytes() != content
    }
    next_manifest = stamp_dates(next_manifest, source_root, republished)

    catalogue_destination = _catalogue_destination(next_manifest)
    catalogue_path = artefacts_root / catalogue_destination.as_posix()
    try:
        catalogue = catalogue_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CatalogueError(f"cannot read catalogue: {error}") from error
    desired_files[catalogue_destination] = replace_generated_catalogue(
        catalogue, render_catalogue(next_manifest)
    ).encode("utf-8")
    desired_files[PurePosixPath(MANIFEST_NAME)] = manifest_to_json(next_manifest)
    _validate_desired_tree(
        artefacts_root.parent.resolve(), artefacts_root, next_manifest, desired_files
    )

    source_by_destination = {
        entry.destination: entry.source for entry in next_manifest.entries
    }
    changes: list[Change] = []
    unchanged: list[PurePosixPath] = []
    for destination, content in desired_files.items():
        current_path = artefacts_root / destination.as_posix()
        if destination == PurePosixPath(MANIFEST_NAME) and head_manifest is not None:
            current: bytes | None = head_manifest
            exists = True
        else:
            exists = current_path.is_file()
            current = current_path.read_bytes() if exists else None
        if exists and current == content:
            unchanged.append(destination)
            continue
        source = source_by_destination.get(destination)
        # Only an update is diffed: an add has nothing to compare against.
        diff = None
        if exists and source is not None and source.suffix.lower() == ".md":
            diff = markdown_diff(current, content) or None
        changes.append(
            Change(
                kind="add" if not exists else "update",
                destination=destination,
                source=source,
                size=len(content),
                url=public_url(next_manifest.site, destination),
                diff=diff,
            )
        )

    deletion_candidates = {entry.destination for entry in reconciliation.missing_entries}
    previous_manifest = published_manifest(head_manifest)
    if previous_manifest is not None:
        next_destinations = {entry.destination for entry in next_manifest.entries}
        deletion_candidates.update(
            entry.destination
            for entry in previous_manifest.entries
            if entry.destination not in next_destinations
        )
    retained_destinations = {
        *desired_files,
        *next_manifest.protected_files,
        *(change.destination for change in changes),
    }
    for destination in sorted(deletion_candidates - retained_destinations, key=str):
        if (artefacts_root / destination.as_posix()).exists():
            changes.append(
                Change(
                    kind="delete",
                    destination=destination,
                    url=public_url(next_manifest.site, destination),
                )
            )

    notes = _source_notes(next_manifest, source_root, desired_files)
    # A destination queued for deletion is managed, not unmanaged. Reporting it as
    # "left alone" would promise the opposite of what this run does to it.
    published, _ = scan_published_tree(artefacts_root)
    expected = {
        *desired_files,
        *next_manifest.protected_files,
        *(change.destination for change in changes if change.kind in DELETION_KINDS),
        *(PurePosixPath(name) for name in CONTROL_FILES),
    }
    for destination in unexpected_published_files(published, expected):
        notes.append(
            Note(
                "orphan",
                f"artefacts/{destination.as_posix()}",
                "in repo, in no manifest, left alone",
            )
        )
    for change in changes:
        if change.kind == "add" and change.size is not None and change.size > LARGE_FILE_BYTES:
            notes.append(Note("size", change.url, "new public file is over 10 MB"))

    return SyncPlan(
        manifest=declared,
        next_manifest=next_manifest,
        desired_files=desired_files,
        changes=tuple(sorted(changes, key=lambda item: (item.kind, str(item.destination)))),
        unchanged=tuple(sorted(unchanged, key=str)),
        excluded=inventory.excluded,
        ignored=tuple((rule, count) for rule, count in ignored_rules if count),
        notes=tuple(notes),
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
        if collection.id in declared_collections
        and declared_collections[collection.id] != collection.order
    ]
    labels.extend(
        f"{entry.id}: {declared_entries[entry.id]} -> {entry.order}"
        for entry in plan.next_manifest.entries
        if entry.id in declared_entries and declared_entries[entry.id] != entry.order
    )
    return labels


_PLAN_GROUPS = (
    ("NEW PUBLIC URLS", ("add",)),
    ("CHANGED", ("update",)),
    ("WILL START 404-ING", ("delete",)),
)


def _human_size(count: int) -> str:
    for unit, step in (("MB", 1024 * 1024), ("KB", 1024)):
        if count >= step:
            return f"{count / step:.1f} {unit}"
    return f"{count} B"


def format_plan(plan: SyncPlan) -> str:
    """The plan grouped by consequence, in full public URLs.

    Grouped by what a reader gains or loses rather than by the operation performed,
    because "this URL will start 404-ing" is the decision being asked for and
    "delete charts/old.png" is not.
    """
    blocks: list[str] = []
    for heading, kinds in _PLAN_GROUPS:
        rows = [change for change in plan.changes if change.kind in kinds]
        if not rows:
            continue
        lines = [f"{heading} ({len(rows)})"]
        for change in sorted(rows, key=lambda item: item.url):
            detail = ""
            if change.kind == "add" and change.size is not None:
                detail = _human_size(change.size)
            elif change.diff:
                detail = change.diff
            elif change.kind == "delete":
                detail = "source deleted"
            lines.append(f"  {change.url}{'  ' + detail if detail else ''}")
        blocks.append("\n".join(lines))

    renumbered = _renumbered_orders(plan)
    if renumbered:
        lines = [f"RENUMBERED ORDER ({len(renumbered)})"]
        lines.extend(f"  {label}" for label in renumbered)
        blocks.append("\n".join(lines))

    # One row per suffix and per rule, not per file: a long path list on every run
    # trains the reader to skip the block, and the rule is what they would edit.
    excluded_rows = [(label, count, "unsupported type") for label, count in plan.excluded]
    excluded_rows += [
        (rule, count, "matched an ignored source rule") for rule, count in plan.ignored
    ]
    if excluded_rows:
        lines = [f"EXCLUDED ({sum(count for _, count, _ in excluded_rows)})"]
        for label, count, reason in excluded_rows:
            files = "1 file" if count == 1 else f"{count} files"
            lines.append(f"  {label:<14} {files}, {reason}")
        blocks.append("\n".join(lines))

    if plan.notes:
        lines = [f"WARNINGS ({len(plan.notes)})"]
        for note in sorted(plan.notes, key=lambda item: (item.kind, item.where)):
            lines.append(f"  {note.kind:<9} {note.where}    {note.detail}")
        blocks.append("\n".join(lines))

    blocks.append(f"UNCHANGED ({len(plan.unchanged)})")
    if not plan.changes:
        blocks.insert(0, "no changes.")
    return "\n\n".join(blocks) + "\n"


def _destination_path(artefacts_root: Path, destination: PurePosixPath) -> Path:
    target = artefacts_root / destination.as_posix()
    _resolve_within(
        artefacts_root.resolve(),
        target,
        ArtefactError,
        f"destination escapes artefacts directory: {destination}",
    )
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


def verify_markdown_round_trip(source_bytes: bytes, rendered: bytes, label: str) -> None:
    """Read the Markdown back out of the written page and compare it to the source.

    The real check, not the earlier one: comparing rendered bytes to the rendered
    bytes just computed proved only that the write landed. Extracting proves the
    escape, the embed and the extraction agree, which is what the published page
    depends on.
    """
    expected = normalise_source_text(source_bytes, label)
    try:
        document = rendered.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValidationError(
            f"{label}: markdown round trip is not UTF-8 ({error})"
        ) from error
    found = extract_markdown(document)
    if found is None:
        raise ValidationError(f"{label}: rendered page carries no markdown block")
    if found != expected:
        raise ValidationError(f"{label}: markdown did not survive the round trip")


def apply_plan(plan: SyncPlan, artefacts_root: Path, source_root: Path | None = None) -> None:
    for change in plan.changes:
        if change.kind not in WRITE_KINDS:
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

    if source_root is None:
        return
    for entry in plan.next_manifest.entries:
        if entry.source.suffix.lower() != ".md":
            continue
        source = source_root / entry.source.as_posix()
        rendered = artefacts_root / entry.destination.as_posix()
        if source.is_file() and rendered.is_file():
            verify_markdown_round_trip(
                source.read_bytes(), rendered.read_bytes(), entry.source.as_posix()
            )


def _read_page(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValidationError(f"cannot parse HTML file {path}: {error}") from error


def _parse_references(text: str) -> _ReferenceParser:
    parser = _ReferenceParser()
    parser.feed(text)
    parser.close()
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
    return _resolve_within(
        repo_root.resolve(),
        target,
        ValidationError,
        f"local reference escapes repository: {reference}",
    )


def validate_repository(repo_root: Path, base_ref: str | None) -> ValidationReport:
    repo_root = repo_root.resolve()
    artefacts_root = repo_root / "artefacts"
    manifest = load_manifest(artefacts_root / MANIFEST_NAME)
    expected = {
        *(PurePosixPath(name) for name in CONTROL_FILES),
        _catalogue_destination(manifest),
        *manifest.protected_files,
        *(entry.destination for entry in manifest.entries),
    }
    actual, ignored_metadata = scan_published_tree(artefacts_root)
    missing = sorted(expected - actual, key=str)
    if missing:
        raise ValidationError(
            "missing published file: " + ", ".join(path.as_posix() for path in missing)
        )
    # An unmanaged file is reported, never rejected and never removed: it may be a
    # hand-written page or a redirect that no manifest entry is meant to explain.
    notes = [
        Note("orphan", f"artefacts/{path.as_posix()}", "in repo, in no manifest, left alone")
        for path in unexpected_published_files(actual, expected)
    ]

    catalogue_destination = _catalogue_destination(manifest)
    catalogue_parser = _parse_references(
        _read_page(artefacts_root / catalogue_destination.as_posix())
    )
    href_counts = Counter(catalogue_parser.hrefs)
    for entry in manifest.entries:
        href = public_href(entry.destination)
        count = href_counts[href]
        if count != 1:
            raise ValidationError(
                f"catalogue link for {entry.id} must appear exactly once, found {count}"
            )

    local_targets: set[Path] = set()
    for relative_path in sorted(actual, key=str):
        if relative_path.suffix.lower() == ".svg":
            validate_svg(
                (artefacts_root / relative_path.as_posix()).read_bytes(),
                f"artefacts/{relative_path.as_posix()}",
            )
        # The template is a control file, not a page: its `$prefix` placeholders make
        # every reference in it unresolvable by design.
        if relative_path.suffix != ".html" or relative_path == PurePosixPath(TEMPLATE_NAME):
            continue
        page = artefacts_root / relative_path.as_posix()
        text = _read_page(page)
        for number, url in external_references(text):
            notes.append(external_note(f"artefacts/{relative_path.as_posix()}:{number}", url))
        parser = _parse_references(text)
        for reference in parser.references:
            target = _resolve_local_reference(repo_root, page, reference)
            if target is None:
                continue
            if not target.is_file():
                raise ValidationError(
                    f"broken local reference in {relative_path}: {reference}"
                )
            local_targets.add(target)

    if base_ref is not None:
        result = subprocess.run(
            ["git", "diff", "--exit-code", f"{base_ref}...HEAD", "--", *HOMEPAGE_FILES],
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
        notes=tuple(notes),
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


def _failure_message(result: CommandResult, failure: str) -> str:
    detail = result.stderr.strip() or result.stdout.strip()
    return f"{failure}: {detail}" if detail else failure


def _run_checked(
    runner: CommandRunner, args: list[str], cwd: Path, failure: str
) -> str:
    result = runner(args, cwd)
    if result.returncode != 0:
        raise PublishError(_failure_message(result, failure))
    return result.stdout


def rebuild_showcase_atlas(
    manifest: Manifest, repo_root: Path, runner: CommandRunner = subprocess_runner
) -> None:
    """Repack the showcase atlas so its panels match the manifest just applied.

    The atlas is generated from the published images and carries each panel's
    title and order, so any applied change can stale it. Manifests that do not
    protect an atlas have no showcase to rebuild and skip this.
    """
    if ATLAS_OUTPUT not in manifest.protected_files:
        return
    print(
        _run_checked(
            runner,
            [sys.executable, ATLAS_SCRIPT, "--repo", str(repo_root)],
            repo_root,
            "cannot rebuild the showcase atlas",
        ).strip()
    )


def _parse_json(output: str, description: str) -> Any:
    """Decode a `gh --json` payload, reporting a parse failure as a `PublishError`."""
    try:
        return json.loads(output)
    except json.JSONDecodeError as error:
        raise PublishError(f"cannot parse {description}") from error


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


def read_head_manifest(
    repo_root: Path, runner: CommandRunner = subprocess_runner
) -> bytes | None:
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
    for _ in range(60):
        result = runner(
            ["gh", "pr", "checks", pull_request_url, "--json", "name,bucket"],
            repo_root,
        )
        if result.returncode == 0:
            parsed = _parse_json(result.stdout, "GitHub checks")
            if not isinstance(parsed, list):
                raise PublishError("cannot parse GitHub checks")
            if any(check.get("name") == "validate" for check in parsed):
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
    checks = _parse_json(output, "GitHub checks")
    if not any(check.get("name") == "validate" for check in checks):
        raise PublishError("required validate check is missing; pull request remains open")
    if any(check.get("bucket") != "pass" for check in checks):
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
        build = _parse_json(output, "GitHub Pages build")
        status = build.get("status")
        if status == "errored" and build.get("commit") == merge_commit:
            message = (build.get("error") or {}).get("message") or "unknown Pages error"
            raise PublishError(f"GitHub Pages build failed: {message}")
        if status == "built" and build.get("commit") == merge_commit:
            return
        sleeper(5)
    raise PublishError("GitHub Pages did not deploy the merge commit within five minutes")


def _restore_main(repo_root: Path, runner: CommandRunner) -> None:
    """Return the local checkout to an up-to-date `main` after a remote merge.

    The merge happens on GitHub, so the checkout is left on the published branch
    and behind `origin/main`, which is exactly what the next publish rejects in
    its preflight. The pull is `--ff-only`, so a `main` that has moved apart for
    another reason is reported rather than merged silently.

    A failure here is a warning, not a `PublishError`: the pull request has
    already merged and the site may already be live, so aborting would hide the
    merge commit and skip Pages verification over local checkout housekeeping.
    """
    for command, failure in (
        (["git", "switch", "main"], "cannot switch back to main"),
        (["git", "pull", "--ff-only", "origin", "main"], "cannot fast-forward main from origin"),
    ):
        result = runner(command, repo_root)
        if result.returncode != 0:
            print(f"Warning: {_failure_message(result, failure)}", file=sys.stderr)
            return


def _public_urls(base_url: str, manifest: Manifest) -> tuple[str, ...]:
    base = base_url.rstrip("/") + "/"
    urls = [base, urljoin(base, "artefacts/")]
    urls.extend(
        urljoin(base, "artefacts/" + public_href(entry.destination))
        for entry in manifest.entries
    )
    # Protected files are reachable URLs too. Every generated Markdown page loads the
    # vendored parser from one of them, so a green publish that never fetched them
    # proved nothing about whether those pages render.
    urls.extend(
        urljoin(base, "artefacts/" + path.as_posix()) for path in manifest.protected_files
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
        read_head_manifest(repo_root, runner),
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
    apply_plan(plan, artefacts_root, source_root)
    rebuild_showcase_atlas(plan.next_manifest, repo_root, runner)
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
        "Only manifest-listed files with an approved extension are published ("
        + ", ".join(sorted(APPROVED_EXTENSIONS))
        + "). Everything else, and local metadata, remains private.\n\n"
        "## Verification\n\n"
        "Local unit tests and repository validation passed before push.\n"
    )
    with tempfile.TemporaryDirectory(prefix="artefact-pr-") as directory:
        body_path = Path(directory) / "body.md"
        body_path.write_text(body, encoding="utf-8")
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
                str(body_path),
            ],
            repo_root,
            "cannot create pull request",
        ).strip()
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
    pr = _parse_json(pr_output, "merged pull request")
    try:
        merge_commit = pr["mergeCommit"]["oid"]
    except (KeyError, TypeError) as error:
        raise PublishError("cannot parse merged pull request") from error
    if pr.get("state") != "MERGED" or not merge_commit:
        raise PublishError("pull request was not merged")

    _restore_main(repo_root, runner)

    repository_output = _run_checked(
        runner,
        ["gh", "repo", "view", "--json", "nameWithOwner"],
        repo_root,
        "cannot identify GitHub repository",
    )
    try:
        repository = _parse_json(repository_output, "GitHub repository")["nameWithOwner"]
    except (KeyError, TypeError) as error:
        raise PublishError("cannot parse GitHub repository") from error
    _wait_for_pages(repo_root, repository, merge_commit, runner, sleeper)
    pages_output = _run_checked(
        runner,
        ["gh", "api", f"repos/{repository}/pages"],
        repo_root,
        "cannot read GitHub Pages configuration",
    )
    try:
        base_url = _parse_json(pages_output, "GitHub Pages URL")["html_url"]
    except (KeyError, TypeError) as error:
        raise PublishError("cannot parse GitHub Pages URL") from error
    urls = _public_urls(base_url, plan.next_manifest)
    _verify_public_urls(repo_root, urls, runner)
    return PublishResult(
        pull_request_url=pull_request_url,
        merge_commit=merge_commit,
        catalogue_url=urljoin(base_url.rstrip("/") + "/", "artefacts/"),
        verified_url_count=len(urls),
        excluded=plan.excluded,
    )


def confirm_and_apply(
    plan: SyncPlan, artefacts_root: Path, confirm, source_root: Path | None = None
) -> bool:
    answer = confirm("Apply these changes? Type yes to continue: ")
    if answer != "yes":
        return False
    apply_plan(plan, artefacts_root, source_root)
    rebuild_showcase_atlas(plan.next_manifest, artefacts_root.parent)
    return True


def handle_unlisted_sources(
    error: UnlistedSourceError,
    manifest_path: Path,
    source_root: Path,
    write: bool,
    confirm: Callable[[str], str],
) -> int:
    """Print derived manifest additions and, for apply and publish, write them.

    Entries whose source file is gone are dropped before the proposal is derived.
    A renamed source arrives here as one unlisted file plus one stale entry, and
    both claim the same destination, so proposing against the unpruned manifest
    fails validation on a duplicate destination instead of recording the rename.
    """
    inventory, _ = apply_source_ignores(
        scan_source(source_root, manifest_path.parent.parent),
        error.manifest.ignored_sources,
    )
    manifest, missing = drop_entries_without_source(
        error.manifest, set(inventory.approved)
    )
    if missing:
        print(f"Entries with no source file, to be dropped ({len(missing)})")
        for entry in missing:
            print(f"  - {entry.id}: {entry.source.as_posix()}")
    proposal = propose_manifest_additions(manifest, error.unlisted, source_root)
    print(format_proposal(proposal))
    if not write:
        print("Run apply or publish to write these manifest additions.")
        return 3
    if confirm("Write these manifest additions? Type yes to continue: ") != "yes":
        print("Cancelled.")
        return 2
    payload = manifest_to_json(merge_manifest_proposal(manifest, proposal))
    manifest_from_bytes(payload, "proposed manifest")
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
    repo_default = default_repo_root()
    source_default = default_source_root()
    for command in ("plan", "apply", "publish"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--repo", type=Path, default=repo_default)
        subparser.add_argument("--source", type=Path, default=source_default)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--repo", type=Path, default=repo_default)
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
            for note in sorted(report.notes, key=lambda item: (item.kind, item.where)):
                print(f"  {note.kind:<9} {note.where}    {note.detail}")
            if args.base_ref:
                print("Homepage files are unchanged.")
            return 0
        if args.command == "publish":
            result = publish(repo_root, args.source, confirm=input_fn)
            if result is None:
                return 0
            excluded = (
                ", ".join(f"{label} ({count})" for label, count in result.excluded)
                or "none"
            )
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
        if not confirm_and_apply(
            plan, artefacts_root, input_fn, args.source.expanduser()
        ):
            print("Cancelled.")
            return 2
        return 0
    except UnlistedSourceError as error:
        try:
            return handle_unlisted_sources(
                error,
                manifest_path,
                args.source.expanduser(),
                args.command in {"apply", "publish"},
                input_fn,
            )
        except ArtefactError as proposal_error:
            print(f"Error: {proposal_error}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Cancelled.", file=sys.stderr)
            return 130
    except ArtefactError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
