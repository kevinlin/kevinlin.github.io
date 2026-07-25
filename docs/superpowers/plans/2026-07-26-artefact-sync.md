# Manifest-Driven Artefact Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one confirmed command that synchronizes `~/Downloads/Artefacts`, publishes through a checked pull request, merges it, waits for GitHub Pages, and verifies every public URL.

**Architecture:** A standard-library Python CLI owns manifest parsing, source discovery, deterministic catalogue generation, safe file application, repository validation, and GitHub publishing. A versioned JSON manifest makes source-to-public mappings explicit, while a GitHub Actions workflow independently validates the committed public tree before the CLI may merge its pull request.

**Tech Stack:** Python 3.12 standard library, `unittest`, JSON, static HTML/CSS, Git, GitHub CLI, GitHub Actions, GitHub Pages, curl

## Global Constraints

- Treat `~/Downloads/Artefacts` as authoritative for approved source files.
- Require one explicit preview confirmation before changing repository files or publishing.
- Publish only `.html`, `.png`, `.jpeg`, `.jpg`, and `.ico` source files.
- Ignore `.DS_Store`, prune nested `kevinlin.github.io` directories, report excluded document types, and reject symbolic links.
- Require every approved source file to have exactly one manifest entry.
- Require unique lowercase kebab-case public paths contained below `artefacts/`.
- Copy binary files byte-for-byte and verify them with SHA-256.
- Apply only exact HTML replacements declared in the manifest.
- Remove trailing spaces and tabs from HTML lines deterministically.
- Never delete vendor files, the catalogue shell, `.nojekyll`, homepage files, or unrelated repository files.
- Do not modify root `index.html`, `styles.css`, or `script.js`.
- Use only the Python standard library in the local CLI and tests.
- Merge only after the expected GitHub validation check exists and all checks pass.

---

## File Map

- Create: `artefacts/manifest.json`, the versioned source-to-public mapping.
- Create: `scripts/artefacts.py`, the only CLI and implementation module.
- Create: `tests/test_artefacts.py`, unit and temporary-directory integration tests.
- Create: `.github/workflows/validate-artefacts.yml`, pull-request validation.
- Modify: `artefacts/index.html`, adding generated-region markers and generated catalogue content.
- Modify: `README.md`, documenting the operator workflow.

### Task 1: Add the Manifest Model and Current Inventory

**Files:**
- Create: `scripts/artefacts.py`
- Create: `tests/test_artefacts.py`
- Create: `artefacts/manifest.json`

**Interfaces:**
- Consumes: JSON from `artefacts/manifest.json`.
- Produces: `load_manifest(path: Path) -> Manifest`, `validate_manifest(manifest: Manifest) -> None`, and immutable `Collection`, `Entry`, and `Manifest` dataclasses.

- [ ] **Step 1: Write failing manifest tests**

Create `tests/test_artefacts.py` with a dynamic import of `scripts/artefacts.py` and tests for a valid manifest, duplicate IDs, duplicate destinations, unknown collections, unsafe relative paths, invalid public paths, unsupported destination extensions, and duplicate order values within one collection.

```python
import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("artefacts_cli", ROOT / "scripts" / "artefacts.py")
artefacts_cli = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = artefacts_cli
SPEC.loader.exec_module(artefacts_cli)


class ManifestTests(unittest.TestCase):
    def write_manifest(self, payload: dict) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_loads_valid_manifest(self):
        path = self.write_manifest({
            "version": 1,
            "protected_files": ["vendor/chart.umd.min.js"],
            "collections": [{
                "id": "charts", "title": "Charts", "description": "Data charts.",
                "section": "Analysis", "section_order": 10, "order": 10
            }],
            "entries": [{
                "id": "cost", "source": "Charts/Cost.png",
                "destination": "charts/cost.png", "title": "Cost",
                "collection": "charts", "order": 10, "replacements": {}
            }]
        })
        manifest = artefacts_cli.load_manifest(path)
        self.assertEqual(manifest.entries[0].destination.as_posix(), "charts/cost.png")

    def test_rejects_duplicate_destinations(self):
        payload = valid_payload_with_two_entries(destination="charts/cost.png")
        with self.assertRaisesRegex(artefacts_cli.ManifestError, "duplicate destination"):
            artefacts_cli.load_manifest(self.write_manifest(payload))
```

Add one focused test method per rejected condition. `valid_payload_with_two_entries` must return two distinct entries unless the named field is intentionally duplicated.

- [ ] **Step 2: Run the tests and verify the expected failure**

Run:

```bash
python3 -m unittest tests/test_artefacts.py -v
```

Expected: import fails because `scripts/artefacts.py` does not exist.

- [ ] **Step 3: Implement manifest types and validation**

Create `scripts/artefacts.py` with these public types and functions:

```python
#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import json
import re

APPROVED_EXTENSIONS = frozenset({".html", ".png", ".jpeg", ".jpg", ".ico"})
PUBLIC_COMPONENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+)?$")


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


def load_manifest(path: Path) -> Manifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest = manifest_from_dict(payload)
    validate_manifest(manifest)
    return manifest
```

Implement `manifest_from_dict(payload: dict) -> Manifest`, `_safe_relative_path(value: str, field_name: str) -> PurePosixPath`, and `validate_manifest(manifest: Manifest) -> None`. Require version `1`; non-empty unique IDs; unique destinations; existing collection IDs; unique collection order and entry order within a collection; HTML destinations named `index.html`; image destination suffix equal to the lowercased source suffix; and lowercase kebab-case destination components. Allow the literal filename `index.html`.

- [ ] **Step 4: Record the complete current inventory**

Create `artefacts/manifest.json` with eight collections in the current catalogue order and these exact entries:

| Collection | Source | Destination | Title |
|---|---|---|---|
| Snapdragon product line | `snapdragon-product-line/Snapdragon 7 Series Timeline.html` | `snapdragon-product-line/index.html` | Snapdragon 7 Series timeline |
| LLM effort level vs. performance | `llm-effort-level-vs-performance/intelligence_index_chart.html` | `llm-effort-level-vs-performance/intelligence-index/index.html` | Intelligence index |
| LLM effort level vs. performance | `llm-effort-level-vs-performance/swe_bench_pro_by_lab_dated_v3.html` | `llm-effort-level-vs-performance/swe-bench-pro-by-lab/index.html` | SWE-bench Pro by lab |
| LLM effort level vs. performance | `llm-effort-level-vs-performance/intelligence_index_chart.png` | `llm-effort-level-vs-performance/intelligence-index-chart.png` | Intelligence index chart |
| LLM effort level vs. performance | `llm-effort-level-vs-performance/GPT-5.6-Avg cost per task.png` | `llm-effort-level-vs-performance/gpt-5-6-average-cost-per-task.png` | GPT-5.6 average cost per task |
| LLM effort level vs. performance | `llm-effort-level-vs-performance/GPT-5.6-Avg output tokens per task.png` | `llm-effort-level-vs-performance/gpt-5-6-average-output-tokens-per-task.png` | GPT-5.6 average output tokens per task |
| LLM effort level vs. performance | `llm-effort-level-vs-performance/Sonnet5-Effor-dial-truth.png` | `llm-effort-level-vs-performance/sonnet-5-effort-dial-truth.png` | Sonnet 5 effort dial |
| GPT-5.6 | `GPT-5.6/GPT-5.6 Sol - The First 5 Days.png` | `gpt-5-6/first-five-days.png` | The first five days |
| Claude Code | `claude-code/The Claude Code Leak.jpeg` | `claude-code/the-claude-code-leak.jpeg` | The Claude Code leak |
| Claude Code | `claude-code/How Claude Code Leaked.jpeg` | `claude-code/how-claude-code-leaked.jpeg` | How Claude Code leaked |
| Claude Code | `claude-code/Claude Code Shortcuts Cheatsheet.jpeg` | `claude-code/shortcuts-cheatsheet.jpeg` | Shortcuts cheatsheet |
| Peter Steinberger: human taste | `peter-steinberger-human-taste/Peter Steinberger on Human Taste_1.png` | `peter-steinberger-human-taste/human-taste-1.png` | Human taste, part 1 |
| Peter Steinberger: human taste | `peter-steinberger-human-taste/Peter Steinberger on Human Taste_2.png` | `peter-steinberger-human-taste/human-taste-2.png` | Human taste, part 2 |
| People Lead lifecycle | `people-lead-lifecycle/People Lead Lifecycle Activities.png` | `people-lead-lifecycle/activities.png` | Lifecycle activities |
| AI trend slop | `ai-trend-slop/TRENDSLOP_When Al Gives You Buzzwords Instead of.png` | `ai-trend-slop/when-ai-gives-you-buzzwords.png` | When AI gives you buzzwords |
| Flow Fabric | `flow-fabric/icons/apple-touch-icon.png` | `flow-fabric/icons/apple-touch-icon.png` | Apple touch icon |
| Flow Fabric | `flow-fabric/icons/favicon-16.png` | `flow-fabric/icons/favicon-16.png` | 16 px favicon |
| Flow Fabric | `flow-fabric/icons/favicon-32.png` | `flow-fabric/icons/favicon-32.png` | 32 px favicon |
| Flow Fabric | `flow-fabric/icons/favicon-64.png` | `flow-fabric/icons/favicon-64.png` | 64 px favicon |
| Flow Fabric | `flow-fabric/icons/favicon.ico` | `flow-fabric/icons/favicon.ico` | ICO favicon |
| Flow Fabric | `flow-fabric/icons/flow-fabric-icon-192.png` | `flow-fabric/icons/flow-fabric-icon-192.png` | 192 px icon |
| Flow Fabric | `flow-fabric/icons/flow-fabric-icon-256.png` | `flow-fabric/icons/flow-fabric-icon-256.png` | 256 px icon |
| Flow Fabric | `flow-fabric/icons/flow-fabric-icon-512.png` | `flow-fabric/icons/flow-fabric-icon-512.png` | 512 px icon |
| Flow Fabric | `flow-fabric/icons/flow-fabric-icon-1024.png` | `flow-fabric/icons/flow-fabric-icon-1024.png` | 1024 px icon |

Use section `Presentations and analysis` for Snapdragon and LLM performance. Use section `Image collections` for the other six collections. Add these protected files:

```json
[
  "vendor/chart.umd.min.js",
  "vendor/chartjs-plugin-datalabels.min.js"
]
```

Declare both cdnjs-to-local replacements on the intelligence-index entry and the Chart.js replacement on the SWE-bench entry.

- [ ] **Step 5: Run manifest tests**

Run:

```bash
python3 -m unittest tests/test_artefacts.py -v
python3 -c 'import json; json.load(open("artefacts/manifest.json"))'
```

Expected: all manifest tests pass and JSON parsing exits 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py artefacts/manifest.json
git commit -m "feat: add artefact manifest model"
```

### Task 2: Plan the Desired Source Tree

**Files:**
- Modify: `scripts/artefacts.py`
- Modify: `tests/test_artefacts.py`

**Interfaces:**
- Consumes: `Manifest`, a source `Path`, and current destination bytes.
- Produces: `scan_source(source_root: Path) -> SourceInventory`, `reconcile_inventory(manifest: Manifest, inventory: SourceInventory) -> SourceReconciliation`, and `build_desired_files(manifest: Manifest, source_root: Path) -> dict[PurePosixPath, bytes]`.

- [ ] **Step 1: Write failing source discovery tests**

Add `SourceInventoryTests` using `tempfile.TemporaryDirectory`. Cover approved extensions case-insensitively, ignored `.DS_Store`, pruned `kevinlin.github.io`, reported `.md`, `.docx`, and `.pdf` suffixes, and rejected symlinks.

```python
def test_scan_reports_only_approved_files(self):
    root = self.make_source()
    (root / "topic" / "Chart.PNG").write_bytes(b"png")
    (root / "topic" / "notes.md").write_text("private", encoding="utf-8")
    (root / ".DS_Store").write_bytes(b"metadata")

    inventory = artefacts_cli.scan_source(root)

    self.assertEqual(inventory.approved, (PurePosixPath("topic/Chart.PNG"),))
    self.assertEqual(inventory.excluded_suffixes, (".md",))
```

Add tests proving an unlisted approved source raises `InventoryError` with a normalized destination suggestion and a missing manifest source becomes a `SourceReconciliation.missing_entries` item rather than an error.

- [ ] **Step 2: Run the new tests and verify they fail**

```bash
python3 -m unittest tests/test_artefacts.py -k SourceInventoryTests -v
```

Expected: FAIL because `scan_source` and `reconcile_inventory` are undefined.

- [ ] **Step 3: Implement source scanning and containment**

Add these immutable types:

```python
@dataclass(frozen=True)
class SourceInventory:
    approved: tuple[PurePosixPath, ...]
    excluded_suffixes: tuple[str, ...]


class InventoryError(ArtefactError):
    pass
```

Implement `scan_source`. Use `os.walk`, remove `kevinlin.github.io` and hidden directory names from `dirs`, ignore `.DS_Store`, collect excluded non-empty suffixes, and sort all returned values. Reject any encountered symlink before extension filtering. Resolve each approved path and require `resolved.is_relative_to(source_root.resolve())`.

Implement `suggest_destination(source: PurePosixPath) -> PurePosixPath`. Normalize directory and stem components to lowercase kebab-case. For HTML return `<normalized-parent>/<normalized-stem>/index.html`; preserve the lowercased image suffix for images.

Add:

```python
@dataclass(frozen=True)
class SourceReconciliation:
    next_manifest: Manifest
    missing_entries: tuple[Entry, ...]
    excluded_suffixes: tuple[str, ...]
```

Implement `reconcile_inventory`. Require exact equality between scanned approved sources and manifest sources that exist. Raise `InventoryError` listing every unlisted approved file and its destination suggestion. Return a `next_manifest` without entries whose sources are missing and retain those entries in `missing_entries` for safe deletion planning.

- [ ] **Step 4: Write failing desired-file tests**

Add tests that prove binary bytes are unchanged, declared HTML replacements occur, a missing replacement raises `TransformationError`, unexpected cdnjs references raise `TransformationError`, and missing entries are omitted from the desired file map.

```python
def test_build_desired_files_preserves_binary_bytes(self):
    source_root, manifest = self.source_with_entry("Images/Card.png", "images/card.png")
    payload = b"\x89PNG\r\n\x1a\ncontent"
    (source_root / "Images" / "Card.png").write_bytes(payload)

    desired = artefacts_cli.build_desired_files(manifest, source_root)

    self.assertEqual(desired[PurePosixPath("images/card.png")], payload)
    self.assertEqual(hashlib.sha256(desired[PurePosixPath("images/card.png")]).digest(), hashlib.sha256(payload).digest())
```

- [ ] **Step 5: Implement deterministic desired-file construction**

Add:

```python
class TransformationError(ArtefactError):
    pass


def transform_html(entry: Entry, source_bytes: bytes) -> bytes:
    text = source_bytes.decode("utf-8")
    for old, new in entry.replacements.items():
        if old not in text:
            raise TransformationError(f"expected replacement not found for {entry.id}: {old}")
        text = text.replace(old, new)
    text = re.sub(r"[ \t]+(?=\r?$)", "", text, flags=re.MULTILINE)
    if "cdnjs.cloudflare.com" in text or "https://cdnjs" in text:
        raise TransformationError(f"forbidden cdnjs reference remains in {entry.id}")
    return text.encode("utf-8")
```

Implement `build_desired_files`. Read binary files with `Path.read_bytes`; call `transform_html` for HTML; skip entries whose source is absent; and verify every binary output hash equals its source hash.

- [ ] **Step 6: Run focused and full tests**

```bash
python3 -m unittest tests/test_artefacts.py -k SourceInventoryTests -v
python3 -m unittest tests/test_artefacts.py -k DesiredTreeTests -v
python3 -m unittest tests/test_artefacts.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py
git commit -m "feat: plan artefact source changes"
```

### Task 3: Generate the Catalogue from the Manifest

**Files:**
- Modify: `scripts/artefacts.py`
- Modify: `tests/test_artefacts.py`
- Modify: `artefacts/index.html`

**Interfaces:**
- Consumes: ordered `Manifest.collections` and `Manifest.entries`.
- Produces: `render_catalogue(manifest: Manifest) -> str` and `replace_generated_catalogue(document: str, generated: str) -> str`.

- [ ] **Step 1: Write failing catalogue tests**

Add `CatalogueTests` covering deterministic section, collection, and entry ordering; HTML escaping in every manifest text field; directory links for HTML `index.html` destinations; direct links for images; exclusion of protected files; exactly one link per entry; and missing or duplicate marker errors.

```python
def test_render_catalogue_escapes_text_and_links_each_entry_once(self):
    manifest = manifest_fixture(title="Cost <script>alert(1)</script>")

    rendered = artefacts_cli.render_catalogue(manifest)

    self.assertIn("Cost &lt;script&gt;alert(1)&lt;/script&gt;", rendered)
    self.assertNotIn("<script>alert(1)</script>", rendered)
    self.assertEqual(rendered.count('href="charts/cost.png"'), 1)
```

- [ ] **Step 2: Run catalogue tests and verify they fail**

```bash
python3 -m unittest tests/test_artefacts.py -k CatalogueTests -v
```

Expected: FAIL because catalogue functions are undefined.

- [ ] **Step 3: Implement catalogue rendering**

Add constants and functions:

```python
CATALOGUE_START = "<!-- ARTEFACTS:START -->"
CATALOGUE_END = "<!-- ARTEFACTS:END -->"


def public_href(destination: PurePosixPath) -> str:
    if destination.name == "index.html":
        return destination.parent.as_posix().rstrip("/") + "/"
    return destination.as_posix()
```

Implement `render_catalogue` with `html.escape(..., quote=True)`. Sort sections by `section_order`, collections by `order`, and entries by `order`. Render the existing semantic `<section>`, `<div class="card-grid">`, and `<article class="card">` structure with the existing titles and descriptions. Do not render empty collections.

Implement `replace_generated_catalogue`. Require exactly one start marker before exactly one end marker and replace only the text between them.

- [ ] **Step 4: Add catalogue markers and regenerate current cards**

In `artefacts/index.html`, replace the two current catalogue sections inside `<main>` with:

```html
        <!-- ARTEFACTS:START -->
        <!-- Content generated from artefacts/manifest.json. -->
        <!-- ARTEFACTS:END -->
```

Then run the renderer once so the generated sections appear between the markers. The rendered page must retain the existing header, styles, footer, home link, eight cards, three presentation links, and 21 image/icon links.

- [ ] **Step 5: Verify catalogue behavior**

```bash
python3 -m unittest tests/test_artefacts.py -k CatalogueTests -v
python3 -m unittest tests/test_artefacts.py -v
test "$(rg -o 'href="[^"]+"' artefacts/index.html | wc -l | tr -d ' ')" = 25
git diff --check
```

Expected: all tests pass, the catalogue has 24 manifest links plus the homepage link, and diff checking exits 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py artefacts/index.html
git commit -m "feat: generate artefact catalogue"
```

### Task 4: Add Safe Plan and Apply Commands

**Files:**
- Modify: `scripts/artefacts.py`
- Modify: `tests/test_artefacts.py`

**Interfaces:**
- Consumes: `SourceReconciliation`, generated catalogue content, manifest bytes from `HEAD`, and CLI arguments.
- Produces: `create_sync_plan(...) -> SyncPlan`, `format_plan(plan: SyncPlan) -> str`, `apply_plan(plan: SyncPlan, manifest_path: Path, artefacts_root: Path) -> None`, and working `plan` and `apply` subcommands.

- [ ] **Step 1: Write failing apply integration tests**

Add `ApplyTests` that use temporary source and artefact roots. Cover add, update, confirmed deletion, manifest pruning after deletion, atomic writes, preservation of protected and unrelated files, no mutation during `plan`, refusal on an unlisted source, and cancellation on any answer other than `yes`.

```python
def test_apply_deletes_only_missing_manifest_destination(self):
    fixture = self.fixture_with_missing_source()
    protected = fixture.artefacts_root / "vendor" / "chart.js"
    unrelated = fixture.artefacts_root / "notes.txt"
    protected.write_bytes(b"vendor")
    unrelated.write_text("keep", encoding="utf-8")

    artefacts_cli.apply_plan(fixture.plan, fixture.manifest_path, fixture.artefacts_root)

    self.assertFalse((fixture.artefacts_root / "images" / "removed.png").exists())
    self.assertEqual(protected.read_bytes(), b"vendor")
    self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
```

- [ ] **Step 2: Run apply tests and verify they fail**

```bash
python3 -m unittest tests/test_artefacts.py -k ApplyTests -v
```

Expected: FAIL because apply and formatting functions are undefined.

- [ ] **Step 3: Implement manifest serialization and preview formatting**

Implement `manifest_to_json(manifest: Manifest) -> bytes` with `json.dumps(..., indent=2, ensure_ascii=False) + "\n"`. Preserve collection and entry ordering.

Implement `format_plan` with stable headings and one path per line:

```text
Add (1)
  + images/new.png
Update (1)
  ~ charts/cost.png
Delete (1)
  - images/removed.png
Unchanged (20)
Excluded source types: .docx, .md, .pdf
```

Add:

```python
@dataclass(frozen=True)
class Change:
    kind: str  # "add", "update", or "delete"
    destination: PurePosixPath


@dataclass(frozen=True)
class SyncPlan:
    manifest: Manifest
    next_manifest: Manifest
    desired_files: dict[PurePosixPath, bytes]
    changes: tuple[Change, ...]
    unchanged: tuple[PurePosixPath, ...]
    excluded_suffixes: tuple[str, ...]
```

Implement `read_head_manifest(repo_root: Path) -> bytes | None` with `git show HEAD:artefacts/manifest.json`. Implement `create_sync_plan(manifest_path: Path, source_root: Path, artefacts_root: Path, head_manifest: bytes | None) -> SyncPlan` by combining source reconciliation, transformed entry bytes, serialized `next_manifest`, and the generated catalogue. Include `manifest.json` and `index.html` in `desired_files`. Compare the generated manifest to `head_manifest`, not the already edited working manifest, so a manifest-only edit is classified as an update. Classify deletes only from `SourceReconciliation.missing_entries`.

- [ ] **Step 4: Implement safe application**

Implement `_atomic_write(path: Path, content: bytes) -> None` with `tempfile.NamedTemporaryFile` in the destination directory, `flush`, `os.fsync`, and `os.replace`. Create only necessary parent directories.

Implement `apply_plan` in this order:

1. Write added and updated `desired_files`, including the generated catalogue and serialized manifest, atomically.
2. Delete only paths in `plan.changes` whose kind is `delete`.
3. Remove only now-empty parent directories below `artefacts_root`, stopping at `artefacts_root`.
4. Re-run repository-free managed-tree validation and raise if the applied result differs from the plan.

Do not use `shutil.rmtree`, recursive globs for deletion, or shell commands.

- [ ] **Step 5: Implement CLI parsing and confirmation**

Add `argparse` subcommands and defaults:

```python
def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_source_root() -> Path:
    return Path.home() / "Downloads" / "Artefacts"
```

Both `plan` and `apply` accept `--repo` and `--source` for tests. They pass `read_head_manifest(repo_root)` into `create_sync_plan`. `plan` prints `format_plan` and exits without writing. `apply` prints the same plan and requires the exact response `yes` before calling `apply_plan`; otherwise print `Cancelled.` and exit 2.

- [ ] **Step 6: Run focused and full tests**

```bash
python3 -m unittest tests/test_artefacts.py -k ApplyTests -v
python3 -m unittest tests/test_artefacts.py -v
python3 scripts/artefacts.py plan
```

Expected: tests pass and the real plan reports no managed-file changes.

- [ ] **Step 7: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py
git commit -m "feat: apply artefact sync plans safely"
```

### Task 5: Validate the Committed Tree in GitHub Actions

**Files:**
- Modify: `scripts/artefacts.py`
- Modify: `tests/test_artefacts.py`
- Create: `.github/workflows/validate-artefacts.yml`

**Interfaces:**
- Consumes: committed manifest, catalogue, artefact files, and optional Git base ref.
- Produces: `validate_repository(repo_root: Path, base_ref: str | None) -> ValidationReport` and the `validate` CLI subcommand.

- [ ] **Step 1: Write failing repository validation tests**

Add `RepositoryValidationTests` for the exact managed path set, missing destination, unexpected publishable file, ignored `.DS_Store`, broken catalogue link, duplicate catalogue link, broken relative HTML script reference, forbidden cdnjs reference, and homepage diff failure supplied by a command runner.

```python
def test_validate_rejects_unexpected_publishable_file(self):
    repo = self.valid_repository()
    (repo / "artefacts" / "unlisted.png").write_bytes(b"png")

    with self.assertRaisesRegex(artefacts_cli.ValidationError, "unexpected published file"):
        artefacts_cli.validate_repository(repo, base_ref=None)
```

- [ ] **Step 2: Run validation tests and verify they fail**

```bash
python3 -m unittest tests/test_artefacts.py -k RepositoryValidationTests -v
```

Expected: FAIL because repository validation is undefined.

- [ ] **Step 3: Implement repository validation**

Add `ValidationError` and `ValidationReport`. `validate_repository` must:

1. Load and validate the manifest.
2. Build the expected set from entry destinations, protected files, `index.html`, and `manifest.json`.
3. Compare with actual files below `artefacts/`, ignoring `.DS_Store` only.
4. Parse the catalogue with a small `html.parser.HTMLParser` subclass and require every manifest href exactly once.
5. Parse every published HTML `src` and local `href`, resolve it relative to the page, and require the target exists below the repository root.
6. Reject cdnjs references in published HTML.
7. When `base_ref` is supplied, run `git diff --exit-code <base_ref>...HEAD -- index.html styles.css script.js` and treat any output or nonzero status as failure.

Return counts for entries, local links, ignored metadata files, and excluded source types recorded by the caller.

- [ ] **Step 4: Add the validate CLI**

Add `validate --repo PATH --base-ref REF`. On success print:

```text
Validated 24 manifest entries and 27 local links.
Homepage files are unchanged.
```

On `ArtefactError`, print one concise error to stderr and exit 1 without a traceback.

- [ ] **Step 5: Create the GitHub Actions workflow**

Create `.github/workflows/validate-artefacts.yml`:

```yaml
name: Validate artefacts

on:
  pull_request:
    paths:
      - ".github/workflows/validate-artefacts.yml"
      - "artefacts/**"
      - "scripts/artefacts.py"
      - "tests/test_artefacts.py"

permissions:
  contents: read

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python3 -m unittest tests/test_artefacts.py -v
      - run: python3 scripts/artefacts.py validate --base-ref "origin/${{ github.base_ref }}"
```

- [ ] **Step 6: Run validation locally**

```bash
python3 -m unittest tests/test_artefacts.py -k RepositoryValidationTests -v
python3 -m unittest tests/test_artefacts.py -v
python3 scripts/artefacts.py validate --base-ref origin/main
git diff --check
```

Expected: tests and repository validation pass with no homepage diff.

- [ ] **Step 7: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py .github/workflows/validate-artefacts.yml
git commit -m "ci: validate published artefacts"
```

### Task 6: Publish, Wait, Merge, and Verify

**Files:**
- Modify: `scripts/artefacts.py`
- Modify: `tests/test_artefacts.py`

**Interfaces:**
- Consumes: up-to-date `main` with either no working changes or one unstaged manifest edit, a confirmed `SyncPlan`, GitHub CLI output, Pages status, and public URLs.
- Produces: `publish(repo_root: Path, source_root: Path, runner: CommandRunner, confirm: Callable[[str], str], now: Callable[[], datetime]) -> PublishResult | None` and the `publish` CLI subcommand.

- [ ] **Step 1: Write failing publishing tests**

Add `PublishingTests` with a recording command runner. Cover missing executables, unauthenticated `gh`, an allowed unstaged manifest edit, rejected staged changes, rejected unrelated worktree changes, branch other than `main`, diverged `main`, a no-change plan exiting before confirmation and branch creation, cancellation before branch creation, exact timestamped branch name, explicit staged paths, ready PR creation, expected-check presence, failed check, successful squash merge, Pages build for the wrong commit, Pages error, public non-200 response, and successful final report.

```python
def test_publish_never_merges_when_expected_check_is_missing(self):
    runner = RecordingRunner(checks=[])

    with self.assertRaisesRegex(artefacts_cli.PublishError, "validate check is missing"):
        artefacts_cli.publish(self.repo, self.source, runner, lambda _: "yes", self.fixed_now)

    self.assertFalse(runner.called(["gh", "pr", "merge"]))
```

The runner is allowed here because Git, GitHub, Pages, and network processes are external boundaries. Assert exact command arguments and returned-state handling rather than mocking internal sync functions.

- [ ] **Step 2: Run publishing tests and verify they fail**

```bash
python3 -m unittest tests/test_artefacts.py -k PublishingTests -v
```

Expected: FAIL because publishing types and functions are undefined.

- [ ] **Step 3: Implement command execution and preflight**

Add:

```python
@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


CommandRunner = Callable[[list[str], Path], CommandResult]


class PublishError(ArtefactError):
    pass
```

Implement `subprocess_runner` with `subprocess.run(..., text=True, capture_output=True, check=False)`. Preflight must run `git`, `gh`, and `curl` version commands; `gh auth status`; `git status --porcelain`; `git branch --show-current`; `git fetch origin main`; and `git rev-list --left-right --count main...origin/main`. Require branch `main` and counts `0 0`. Permit either an empty status or exactly ` M artefacts/manifest.json`; reject staged changes and every other worktree path before planning.

- [ ] **Step 4: Implement confirmed branch, commit, and PR creation**

After preflight, call `create_sync_plan` and print the plan. If there are no additions, updates, or deletions, print `No artefact changes to publish.` and return `None` before confirmation or branch creation. Otherwise ask:

```text
Apply these changes and publish them? Type yes to continue:
```

On `yes`, create `agent/sync-artefacts-YYYYMMDD-HHMMSS`, call `apply_plan`, run unit tests and `validate`, stage only `artefacts/manifest.json`, `artefacts/index.html`, and paths from the plan, and commit `chore: sync artefacts`. Push with tracking.

Write the PR body to a temporary file and run:

```bash
gh pr create --base main --head BRANCH --title "Sync published artefacts" --body-file BODY_FILE
```

The body must contain the add/update/delete counts, excluded types, local validation results, and the privacy boundary.

- [ ] **Step 5: Implement check gating and merge**

Run `gh pr checks PR_URL --watch --fail-fast`, then `gh pr checks PR_URL --json name,bucket`. Parse JSON and require a check named `validate`, at least one returned check, and every bucket equal to `pass`. Any missing, cancelled, skipped, pending, or failed check stops with the PR left open.

Only after that gate, run:

```bash
gh pr merge PR_URL --squash
gh pr view PR_URL --json state,mergeCommit,url
```

Require state `MERGED` and a non-empty merge commit OID.

- [ ] **Step 6: Implement Pages polling and public verification**

Derive `owner/name` from `gh repo view --json nameWithOwner`. Poll `gh api repos/OWNER/REPO/pages/builds/latest` every five seconds for at most five minutes. Succeed only when status is `built` and commit equals the PR merge commit. Fail immediately on `errored`; continue polling `building`, `queued`, or an older commit.

Build a unique URL list containing the homepage, catalogue, and every manifest entry. Use `curl --silent --show-error --location --output /dev/null --write-out %{http_code}` and require 200 for every URL.

Return:

```python
@dataclass(frozen=True)
class PublishResult:
    pull_request_url: str
    merge_commit: str
    catalogue_url: str
    verified_url_count: int
    excluded_suffixes: tuple[str, ...]
```

- [ ] **Step 7: Add the publish CLI and concise output**

Wire `publish` into `main()`. Catch `KeyboardInterrupt` and `ArtefactError`, print a concise message, and exit nonzero. On success print the PR URL, merge commit, catalogue URL, verified URL count, and excluded types. Do not delete the local or remote branch automatically.

- [ ] **Step 8: Run focused and full tests**

```bash
python3 -m unittest tests/test_artefacts.py -k PublishingTests -v
python3 -m unittest tests/test_artefacts.py -v
python3 scripts/artefacts.py plan
python3 scripts/artefacts.py validate --base-ref origin/main
```

Expected: all tests pass, the current source plan is unchanged, and repository validation passes.

- [ ] **Step 9: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py
git commit -m "feat: publish artefact sync automatically"
```

### Task 7: Document and Verify the Complete Operator Workflow

**Files:**
- Modify: `README.md`
- Verify: every file in the File Map

**Interfaces:**
- Consumes: the complete CLI and CI workflow.
- Produces: copy-ready operating instructions and final verification evidence.

- [ ] **Step 1: Write README usage documentation**

Add an `Artefact publishing` section documenting:

```markdown
## Artefact publishing

1. Add, replace, rename, or remove approved files below `~/Downloads/Artefacts`.
2. Update `artefacts/manifest.json` for new files, renamed sources, metadata changes, or intentional public URL changes.
3. Preview without changing anything:

   ```bash
   python3 scripts/artefacts.py plan
   ```

4. Publish and type `yes` only after reviewing every addition, update, and deletion:

   ```bash
   python3 scripts/artefacts.py publish
   ```

The command stops without merging if local validation, GitHub checks, GitHub Pages, or public URL verification fails. Markdown, Word, PDF, metadata, and unlisted files are not published.
```

Also document that public destination paths are stable and must not be changed merely because a source filename changes.

- [ ] **Step 2: Run the full automated suite**

```bash
python3 -m unittest tests/test_artefacts.py -v
python3 scripts/artefacts.py plan
python3 scripts/artefacts.py validate --base-ref origin/main
```

Expected: every test passes, `plan` reports no managed changes, and validation reports 24 manifest entries with no homepage changes.

- [ ] **Step 3: Exercise a temporary add/update/delete cycle**

Use the integration-test fixture through this focused test:

```bash
python3 -m unittest tests/test_artefacts.py \
  -k test_complete_add_update_delete_cycle -v
```

Expected: PASS and the fixture proves preview, confirmation, application, manifest pruning, catalogue regeneration, and protected-file preservation.

- [ ] **Step 4: Verify repository scope and workflow syntax**

```bash
git diff --check origin/main...HEAD
git diff --exit-code origin/main...HEAD -- index.html styles.css script.js
python3 -c 'import json; json.load(open("artefacts/manifest.json"))'
git status --short
```

Expected: diff checking and homepage isolation exit 0, the manifest parses, and only intended plan files are changed.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: explain artefact publishing"
```

- [ ] **Step 6: Run final verification before integration**

```bash
python3 -m unittest tests/test_artefacts.py -v
python3 scripts/artefacts.py plan
python3 scripts/artefacts.py validate --base-ref origin/main
git diff --check origin/main...HEAD
git status --short --branch
```

Expected: all tests pass, the real sync plan is unchanged, repository validation and diff checking pass, and the branch is clean.
