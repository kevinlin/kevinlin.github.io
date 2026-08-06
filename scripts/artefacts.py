#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime
import difflib
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


# Sources that become a generated page rather than a byte copy. Both publish to a
# directory index.html so the public URL carries no file extension.
DIRECTORY_INDEX_EXTENSIONS = frozenset({".html", ".md"})
APPROVED_EXTENSIONS = frozenset({".html", ".md", ".png", ".jpeg", ".jpg", ".ico"})
PUBLIC_COMPONENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+)?$")
PROTECTED_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
CDNJS_HOST = "cdnjs.cloudflare.com"
IGNORED_METADATA_NAME = ".DS_Store"
DELETION_KINDS = frozenset({"delete", "orphan"})
WRITE_KINDS = frozenset({"add", "update"})
HOMEPAGE_FILES = ("index.html", "styles.css", "script.js")


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
    # Source paths the scan subtracts before reconciliation. A trailing "/" makes a
    # rule cover a subtree. Kept as written rather than as PurePosixPath, because
    # PurePosixPath discards the trailing separator the two forms are told apart by.
    ignored_sources: tuple[str, ...] = ()


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
    markdown_diffs: tuple[tuple[PurePosixPath, str], ...] = ()
    ignored_sources: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    entry_count: int
    local_link_count: int
    ignored_metadata_count: int


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

    ignored_payload = payload.get("ignored_sources", [])
    if not isinstance(ignored_payload, list):
        raise ManifestError("ignored_sources must be an array")
    ignored_sources = tuple(
        _safe_ignore_rule(value) for value in ignored_payload
    )

    return Manifest(
        version=payload.get("version"),
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
    if not all(pattern.fullmatch(component) for component in path.parts):
        raise ManifestError(message)


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
            _resolve_within(
                resolved_root,
                candidate,
                InventoryError,
                f"source path escapes source directory: {candidate}",
            )
            approved.append(PurePosixPath(candidate.relative_to(source_root).as_posix()))

    return SourceInventory(
        approved=tuple(sorted(approved, key=str)),
        excluded_suffixes=tuple(sorted(excluded_suffixes)),
    )


def _is_ignored(source: PurePosixPath, rules: tuple[str, ...]) -> bool:
    text = source.as_posix()
    return any(
        text.startswith(rule) if rule.endswith("/") else text == rule
        for rule in rules
    )


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

# One self-contained document per Markdown entry, matching artefacts/index.html:
# same colour tokens and fonts, same pre-paint theme script, a 72rem content column, and a
# back-link to the catalogue. The CSS is inline because every other published page
# is self-contained; a shared stylesheet would add a cross-file reference for
# `validate` to resolve on every document.
MARKDOWN_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} | Artefacts</title>
    <link rel="icon" type="image/svg+xml" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="20" fill="%230063a3"/><text x="50" y="75" font-size="60" text-anchor="middle" fill="white" font-family="sans-serif" font-weight="bold">K</text></svg>'>
    <meta name="theme-color" content="#0063a3">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script>
        // Applied before first paint so the page never flashes the wrong theme.
        (function () {{
            try {{
                var stored = localStorage.getItem('theme');
                var dark = stored ? stored === 'dark'
                    : window.matchMedia('(prefers-color-scheme: dark)').matches;
                if (dark) document.documentElement.setAttribute('data-theme', 'dark');
            }} catch (e) {{}}
        }})();
    </script>
    <style>
        :root {{
            color-scheme: light;
            --primary-color: #0063a3;
            --accent-color: #ff5a5f;
            --text-color: #333333;
            --light-text: #666666;
            --background-color: #ffffff;
            --section-bg: #f8f9fa;
            --border-color: #e6e6e6;
            --tint: rgba(0, 99, 163, 0.1);
        }}

        [data-theme="dark"] {{
            color-scheme: dark;
            --primary-color: #4389b9;
            --accent-color: #ff8085;
            --text-color: #f8f9fa;
            --light-text: #cccccc;
            --background-color: #121212;
            --section-bg: #1e1e1e;
            --border-color: #3a3a3a;
            --tint: rgba(67, 137, 185, 0.16);
        }}

        *, *::before, *::after {{ box-sizing: border-box; }}

        html {{
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            text-rendering: optimizeLegibility;
        }}

        body {{
            margin: 0;
            background: var(--section-bg);
            color: var(--text-color);
            font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 1rem;
            line-height: 1.7;
        }}

        header, main, footer {{
            width: min(72rem, calc(100% - 48px));
            margin-inline: auto;
        }}

        header {{ padding: 56px 0 8px; }}

        a {{ color: var(--primary-color); text-underline-offset: 0.2em; }}
        a:hover {{ color: var(--accent-color); }}

        .back-link {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            min-height: 44px;
            font-weight: 500;
            text-decoration: none;
        }}

        .back-link:hover span {{ text-decoration: underline; }}

        h1 {{
            margin: 24px 0 8px;
            font-size: clamp(1.9rem, 5vw, 2.6rem);
            line-height: 1.2;
            text-wrap: balance;
        }}

        main {{ padding-bottom: 72px; }}

        article {{
            padding: 32px;
            background: var(--background-color);
            border: 1px solid var(--border-color);
            border-radius: 12px;
        }}

        article > :first-child {{ margin-top: 0; }}
        article > :last-child {{ margin-bottom: 0; }}

        article h1, article h2, article h3, article h4 {{
            margin: 2em 0 0.6em;
            line-height: 1.3;
            text-wrap: balance;
        }}

        article h2 {{
            padding-bottom: 0.3em;
            border-bottom: 1px solid var(--border-color);
            font-size: 1.5rem;
        }}

        article h3 {{ font-size: 1.2rem; }}

        article img {{ max-width: 100%; height: auto; }}

        article blockquote {{
            margin: 1.5em 0;
            padding: 0.2em 1.2em;
            border-left: 3px solid var(--primary-color);
            color: var(--light-text);
        }}

        article code {{
            padding: 0.15em 0.4em;
            background: var(--tint);
            border-radius: 4px;
            font-size: 0.9em;
        }}

        article pre {{
            overflow-x: auto;
            padding: 16px;
            background: var(--section-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
        }}

        article pre code {{ padding: 0; background: none; }}

        .table-scroll, article table {{ display: block; overflow-x: auto; }}

        article table {{ border-collapse: collapse; width: 100%; }}

        article th, article td {{
            padding: 8px 12px;
            border: 1px solid var(--border-color);
            text-align: left;
        }}

        article th {{ background: var(--tint); }}

        article hr {{ border: none; border-top: 1px solid var(--border-color); }}

        footer {{
            padding-bottom: 48px;
            color: var(--light-text);
            font-size: 0.9rem;
        }}
    </style>
</head>
<body>
    <header>
        <a class="back-link" href="{prefix}">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M19 12H5M12 19l-7-7 7-7"/>
            </svg>
            <span>Back to Artefacts</span>
        </a>
        <h1>{title}</h1>
    </header>

    <main>
        <article id="markdown-body"></article>
    </main>

    <footer>
        <p>Rendered from Markdown in the browser.</p>
    </footer>

{block_start}{markdown}{block_end}
    <script src="{prefix}{vendor}"></script>
    <script>
        // The source block carries the Markdown verbatim except for two escaped
        // sequences; the same substitution runs in reverse here. See
        // escape_markdown_block in scripts/artefacts.py.
        (function () {{
            // textContent starts at the newline that follows the opening tag, which
            // the Python-side extract_markdown slice does not include. Drop it so
            // both sides see the same bytes.
            var raw = document.getElementById('markdown-source').textContent.replace(/^\\n/, '');
            var text = raw.replace(/<(\\\\+)(\\/script|!--)/gi, function (match, slashes, marker) {{
                return '<' + slashes.slice(1) + marker;
            }});
            var body = document.getElementById('markdown-body');
            body.innerHTML = marked.parse(text);
            // Most documents open with their own H1, and the proposal derives the
            // manifest title from exactly that heading, so the page would print the
            // title twice. Drop the article's copy when it repeats the header rather
            // than stripping it from the source, which has to stay byte-exact.
            // The H1 is not always the first element — several sources open with a
            // banner line — so match on the first H1 anywhere in the article. Equal
            // text is the whole test: a heading that differs really does say
            // something else, and both copies stay.
            var lead = body.querySelector('h1');
            var heading = document.querySelector('header h1');
            if (lead && heading &&
                lead.textContent.trim() === heading.textContent.trim()) {{
                lead.remove();
            }}
        }})();
    </script>
</body>
</html>
"""


def markdown_vendor_path(manifest: Manifest) -> PurePosixPath:
    for path in manifest.protected_files:
        if path.name == MARKDOWN_VENDOR_NAME:
            return path
    raise TransformationError(
        f"{MARKDOWN_VENDOR_NAME} must be listed in protected_files to publish Markdown"
    )


def render_markdown_page(
    entry: Entry, source_bytes: bytes, vendor_path: PurePosixPath
) -> bytes:
    """One self-contained page carrying the Markdown verbatim.

    The Markdown is embedded rather than converted because this script is
    standard-library only. Its bytes are preserved exactly: the trailing-space
    stripping `transform_html` applies would turn a Markdown hard line break into a
    soft one, and both `apply`'s byte check and the diff preview depend on the
    embed-extract round trip being lossless.
    """
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TransformationError(
            f"Markdown source is not UTF-8: {entry.source}"
        ) from error
    if text and not text.endswith("\n"):
        text += "\n"
    prefix = "../" * len(entry.destination.parent.parts)
    document = MARKDOWN_PAGE_TEMPLATE.format(
        title=html.escape(entry.title),
        prefix=prefix,
        vendor=vendor_path.as_posix(),
        block_start=MARKDOWN_BLOCK_START,
        markdown=escape_markdown_block(text),
        block_end=MARKDOWN_BLOCK_END,
    )
    if has_cdnjs_reference(document):
        raise TransformationError(
            f"forbidden cdnjs reference in generated Markdown page for {entry.id}"
        )
    return document.encode("utf-8")


MARKDOWN_DIFF_LINE_LIMIT = 40


def markdown_diff(
    published: bytes | None,
    source: bytes,
    limit: int = MARKDOWN_DIFF_LINE_LIMIT,
) -> str:
    """Unified diff between the published Markdown and the new source.

    The published page is the same basis the byte comparison uses, so the diff
    cannot disagree with the change classification. Truncation is stated rather
    than silent: a cut-off diff that looked complete would be worse than no diff.
    """
    if published is None:
        return ""
    try:
        document = published.decode("utf-8")
    except UnicodeDecodeError:
        return "diff unavailable: published page is not UTF-8"
    previous = extract_markdown(document)
    if previous is None:
        return "diff unavailable: published page has no embedded Markdown"
    try:
        current = source.decode("utf-8")
    except UnicodeDecodeError:
        return "diff unavailable: source is not UTF-8"
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
PLACEHOLDER_DESCRIPTION = "TODO: describe this collection."
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
            label = group or sources[0].stem
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
            source_path = source_root / source.as_posix()
            title = _derive_title(source.stem)
            suffix = source.suffix.lower()
            if suffix == ".html":
                replacements, unmapped = _vendor_replacements(
                    vendor_by_name, source_path, destination
                )
                if unmapped:
                    warnings[entry_id] = (
                        "an unmapped cdnjs reference remains; vendor it into "
                        "protected_files or the next run fails in transform_html"
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


def transform_html(entry: Entry, source_bytes: bytes) -> bytes:
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TransformationError(f"HTML source is not UTF-8: {entry.source}") from error
    for old, new in entry.replacements.items():
        parts = text.split(old)
        if len(parts) == 1:
            raise TransformationError(
                f"expected replacement not found for {entry.id}: {old}"
            )
        text = new.join(parts)
    text = TRAILING_SPACE.sub("", text)
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
        _resolve_within(
            resolved_root,
            source_path,
            InventoryError,
            f"source path escapes source directory: {source_path}",
        )
        source_bytes = source_path.read_bytes()
        suffix = entry.source.suffix.lower()
        if suffix == ".html":
            output = transform_html(entry, source_bytes)
        elif suffix == ".md":
            # Looked up per entry on purpose: a manifest with no Markdown must not
            # require the parser to be vendored.
            output = render_markdown_page(
                entry, source_bytes, markdown_vendor_path(manifest)
            )
        else:
            output = source_bytes
        desired[entry.destination] = output
    return desired


def public_href(destination: PurePosixPath) -> str:
    if destination.name == "index.html":
        return destination.parent.as_posix().rstrip("/") + "/"
    return destination.as_posix()


def collect_source_timestamps(
    manifest: Manifest, source_root: Path
) -> dict[str, str]:
    """Last-modified date per entry id, read from the source file at run time.

    The date is not manifest content: it is whatever the filesystem reports when
    the command runs, so a re-downloaded source refreshes its card without a
    manual edit. A source that is missing (a proposed deletion) contributes
    nothing and its card falls back to the remaining entries.
    """
    timestamps: dict[str, str] = {}
    for entry in manifest.entries:
        source_path = source_root / entry.source.as_posix()
        try:
            modified = source_path.stat().st_mtime
        except OSError:
            continue
        timestamps[entry.id] = date.fromtimestamp(modified).isoformat()
    return timestamps


def render_catalogue(
    manifest: Manifest, timestamps: dict[str, str] | None = None
) -> str:
    timestamps = timestamps or {}
    entries_by_collection: dict[str, list[Entry]] = {}
    for entry in manifest.entries:
        entries_by_collection.setdefault(entry.collection, []).append(entry)

    sections: dict[tuple[int, str], list[Collection]] = {}
    for collection in manifest.collections:
        if entries_by_collection.get(collection.id):
            sections.setdefault(
                (collection.section_order, collection.section), []
            ).append(collection)

    # ISO dates sort chronologically, so the card carries its newest source and the
    # section orders its cards by that string. A collection with no readable source
    # has no date and falls to the bottom on its declared order.
    latest_by_collection = {
        collection_id: max(
            (timestamps[entry.id] for entry in entries if entry.id in timestamps),
            default="",
        )
        for collection_id, entries in entries_by_collection.items()
    }

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
                    f"                    <p>{html.escape(collection.description)}</p>",
                ]
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


def manifest_to_json(manifest: Manifest) -> bytes:
    payload = {
        "version": manifest.version,
        "protected_files": [path.as_posix() for path in manifest.protected_files],
        "ignored_sources": list(manifest.ignored_sources),
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


def manifest_from_bytes(content: bytes, description: str) -> Manifest:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read {description}: {error}") from error
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
        for name in HOMEPAGE_FILES:
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
    inventory, ignored_rules = apply_source_ignores(
        scan_source(source_root), manifest.ignored_sources
    )
    reconciliation = reconcile_inventory(manifest, inventory)
    next_manifest = reconciliation.next_manifest
    desired_files = build_desired_files(next_manifest, source_root)

    catalogue_path = artefacts_root / "index.html"
    try:
        catalogue = catalogue_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CatalogueError(f"cannot read catalogue: {error}") from error
    generated_catalogue = replace_generated_catalogue(
        catalogue,
        render_catalogue(
            next_manifest, collect_source_timestamps(next_manifest, source_root)
        ),
    ).encode("utf-8")
    desired_files[PurePosixPath("index.html")] = generated_catalogue
    desired_files[PurePosixPath("manifest.json")] = manifest_to_json(next_manifest)
    _validate_desired_tree(
        artefacts_root.parent.resolve(), artefacts_root, next_manifest, desired_files
    )

    changes: list[Change] = []
    unchanged: list[PurePosixPath] = []
    for destination, content in desired_files.items():
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
        previous_manifest = manifest_from_bytes(head_manifest, "HEAD manifest")
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

    # Only updates are diffed. An add has nothing to compare against, and a delete
    # or orphan has no source left to read.
    markdown_sources = {
        entry.destination: entry.source
        for entry in next_manifest.entries
        if entry.source.suffix.lower() == ".md"
    }
    updated = {change.destination for change in changes if change.kind == "update"}
    markdown_diffs: list[tuple[PurePosixPath, str]] = []
    for destination in sorted(updated & markdown_sources.keys(), key=str):
        published_path = artefacts_root / destination.as_posix()
        source_path = source_root / markdown_sources[destination].as_posix()
        body = markdown_diff(
            published_path.read_bytes() if published_path.is_file() else None,
            source_path.read_bytes(),
        )
        if body:
            markdown_diffs.append((destination, body))

    return SyncPlan(
        manifest=declared,
        next_manifest=next_manifest,
        desired_files=desired_files,
        changes=tuple(sorted(changes, key=lambda item: (item.kind, str(item.destination)))),
        unchanged=tuple(sorted(unchanged, key=str)),
        excluded_suffixes=inventory.excluded_suffixes,
        markdown_diffs=tuple(markdown_diffs),
        ignored_sources=ignored_rules,
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
    if plan.markdown_diffs:
        lines.append(f"Markdown changes ({len(plan.markdown_diffs)})")
        for destination, body in plan.markdown_diffs:
            lines.append(f"  ~ {destination.as_posix()}")
            lines.extend(f"      {line}" for line in body.splitlines())
    if plan.ignored_sources:
        # One line per rule, not per file: a long path list on every run trains the
        # reader to skip the block, and the rule is what they would edit.
        total = sum(count for _, count in plan.ignored_sources)
        lines.append(f"Ignored sources ({total})")
        lines.extend(
            f"  - {rule} ({count} file{'' if count == 1 else 's'})"
            for rule, count in plan.ignored_sources
        )
    lines.append(f"Unchanged ({len(plan.unchanged)})")
    lines.append(f"Excluded source types: {', '.join(plan.excluded_suffixes) or 'none'}")
    return "\n".join(lines)


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


def apply_plan(plan: SyncPlan, artefacts_root: Path) -> None:
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

    catalogue_parser = _parse_references(_read_page(artefacts_root / "index.html"))
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
        if relative_path.suffix != ".html":
            continue
        page = artefacts_root / relative_path.as_posix()
        text = _read_page(page)
        if has_cdnjs_reference(text):
            raise ValidationError(f"forbidden cdnjs reference in {relative_path}")
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
    apply_plan(plan, artefacts_root)
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
        "Only manifest-listed HTML, Markdown, PNG, JPEG, JPG, and ICO files are "
        "published. Excluded document types and local metadata remain private.\n\n"
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
        excluded_suffixes=plan.excluded_suffixes,
    )


def confirm_and_apply(plan: SyncPlan, artefacts_root: Path, confirm) -> bool:
    answer = confirm("Apply these changes? Type yes to continue: ")
    if answer != "yes":
        return False
    apply_plan(plan, artefacts_root)
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
        scan_source(source_root), error.manifest.ignored_sources
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
        if not confirm_and_apply(plan, artefacts_root, input_fn):
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
