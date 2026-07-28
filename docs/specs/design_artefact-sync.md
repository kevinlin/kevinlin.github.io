# Manifest-Driven Artefact Sync

## Goal

Provide one repeatable command that synchronizes approved files from `~/Downloads/Artefacts` to the GitHub Pages `artefacts/` tree, updates the catalogue, publishes through a pull request, merges after checks pass, and verifies the deployed URLs.

The local source directory is authoritative. Removing a managed source file proposes removal of its manifest entry and published file. No file is added, updated, deleted, committed, or published until the user approves a complete preview.

## Selected Approach

Use a repository-owned JSON manifest and a local Python command. The manifest keeps public paths, titles, collections, and ordering explicit. The command discovers content changes, builds and validates the desired tree in a temporary directory, shows the exact plan, and applies it only after confirmation.

This approach is preferred over direct directory mirroring because source filenames may change while public URLs must remain stable. It is preferred over cloud-side ingestion because GitHub Actions cannot access the user's local Downloads directory without another storage and credential system.

## Repository Structure

```text
artefacts/
  manifest.json
  index.html
  vendor/
  ...published files...
scripts/
  artefacts.py
tests/
  test_artefacts.py
.github/workflows/
  validate-artefacts.yml
```

`scripts/artefacts.py` is the single command-line entry point. It uses Python's standard library and exposes `plan`, `apply`, `validate`, and `publish` subcommands. The user-facing command is:

```bash
python3 scripts/artefacts.py publish
```

## Manifest

`artefacts/manifest.json` has a versioned schema with an explicit protected-file list, ordered collections, and entries. The protected-file list contains repository-owned runtime assets such as the vendored Chart.js files. Each entry contains:

- a stable identifier;
- a source path relative to `~/Downloads/Artefacts`;
- a destination path relative to `artefacts/`;
- a display title;
- a collection identifier;
- an explicit order;
- optional exact HTML replacements for local runtime dependencies.

Order values are hand-edited, so a merged or pasted block collides. `plan` renumbers the affected collection or section to 10, 20, 30 … rather than aborting. The new sequence is the one the group already renders in: declared order first, manifest position as the tie-break, so a pasted duplicate lands beside the item it was copied from. A group whose orders already read unambiguously keeps its numbers and its gaps. Every rewrite is listed under `Renumbered order` and reaches the file through the normal `manifest.json` change, so confirmation still gates it.

Example:

```json
{
  "version": 1,
  "collections": [
    {
      "id": "llm-performance",
      "title": "LLM effort level vs. performance",
      "description": "Interactive comparisons and supporting charts.",
      "order": 20
    }
  ],
  "entries": [
    {
      "id": "intelligence-index",
      "source": "llm-effort-level-vs-performance/intelligence_index_chart.html",
      "destination": "llm-effort-level-vs-performance/intelligence-index/index.html",
      "title": "Intelligence index",
      "collection": "llm-performance",
      "order": 10,
      "replacements": {
        "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js": "../../vendor/chart.umd.min.js"
      }
    }
  ]
}
```

JSON is selected because Python can parse it without installing dependencies and its schema is explicit in code review.

## Source and Destination Rules

The command recursively scans the source directory for `.html`, `.png`, `.jpeg`, `.jpg`, and `.ico` files. It prunes nested repository copies, ignores metadata such as `.DS_Store`, reports excluded document types, and rejects symbolic links.

The following rules apply:

- Every approved source file must have one manifest entry. An unlisted source blocks publication and prints a complete derived entry, plus a derived collection when the source folder maps to none. See [Manifest Proposals](#manifest-proposals).
- Every manifest source must exist. A missing source is shown as a deletion and, after confirmation, removes its entry and destination.
- Every published file must be explained by the manifest. A file under `artefacts/` that the desired tree does not contain is shown as an orphan deletion. See [Orphan Cleanup](#orphan-cleanup).
- Destination paths must be unique, relative, lowercase kebab-case, and contained below `artefacts/`.
- HTML presentations use a directory `index.html`; images retain an approved image extension.
- Binary files are copied byte-for-byte and verified with SHA-256.
- HTML replacements are exact manifest declarations. An expected replacement that is absent is an error.
- HTML lines have trailing spaces and tabs removed deterministically so generated commits pass Git whitespace checks.
- Published HTML must not contain forbidden cdnjs runtime references after transformation.
- Vendor files and the catalogue shell are protected and are never deleted by source mirroring.
- Files outside the allowlist are never copied.

Renaming a source does not require changing its public URL. Only the manifest's `source` field changes. Changing a `destination` is treated as an explicit public URL migration and appears as one deletion and one addition in the preview.

## Manifest Proposals

### Why a bare error is not enough

An approved source file with no manifest entry aborts every command. A bare error naming the source and a suggested destination leaves every other field (id, title, collection, order, replacements) and any missing collection block to be hand-written before the command runs again. Adding a folder of images becomes a long manual edit against a schema the script already knows.

### What the proposal does

Turn the block into a proposal. The script derives a complete, schema-valid manifest addition, shows it, and on confirmation writes it to `artefacts/manifest.json`. The user edits the placeholder prose and re-runs.

Not in scope: publishing without a second, deliberate run; deriving publication-quality titles or descriptions (derived prose is a starting point the user is expected to edit); changing `validate`, which never sees the local source directory.

### Error carries data

`UnlistedSourceError(InventoryError)` carries the `manifest` it was reconciled against and `unlisted: tuple[PurePosixPath, ...]`. `reconcile_inventory` raises it instead of the bare `InventoryError`, and its message is a bare summary line.

The old message spelled out each source and its suggested destination. That text is gone: `main()` catches `UnlistedSourceError` above the generic `ArtefactError` printer, so nothing would ever render it, and a second formatter for the same condition drifts from `format_proposal` unnoticed. The condition has one representation — data on the error, formatting in `format_proposal`.

Carrying the manifest keeps `handle_unlisted_sources` from re-reading and re-validating the file `create_sync_plan` loaded moments earlier. Subclassing still holds for anything catching `InventoryError`; only `main()` is aware of the new type.

### Derivation

One pure function:

```python
propose_manifest_additions(
    manifest: Manifest,
    unlisted: tuple[PurePosixPath, ...],
    source_root: Path,
) -> ManifestProposal          # .collections, .entries
```

It reads source files only to detect vendor references. It does not write.

#### Collection match

A source's top-level folder maps to the collection used by existing entries whose `source` shares that folder. Folder-name-to-collection-id matching is wrong here: `llm-effort-level-vs-performance/` already maps to collection id `llm-performance`, and a naive match would invent a duplicate collection.

Unmatched folders produce a new collection:

- `id` — slugged folder name.
- `title` — folder name, title-cased.
- `description` — `TODO: describe this collection.`
- `section` — the section existing collections of the same media type already sit in. A folder with any `.html` source is a presentation collection, otherwise an image collection; existing collections are classified the same way, by the extension of their entries' destinations. Only when the manifest holds no collection of that media type do the `Presentations and analysis` / `Image collections` constants apply.
- `section_order` — reused from existing collections in that section; `max + 10` when the section itself is new.
- `order` — `max + 10` among collections in that section.

Section names are manifest content, so they are learned rather than matched against the constants. Renaming a section in `manifest.json` is a content edit with no reason to touch Python; if the constants were authoritative, the next proposal would create a second section beside the renamed one under the stale name, splitting that media type across two catalogue headings.

#### Entry fields

- `source` is the scanned path; `destination` comes from the existing `suggest_destination`.
- `id` — slugged destination minus extension, `/` replaced with `-`; `-2`, `-3` … on collision with an existing or proposed id.
- `title` — source stem with a leading `NN-` ordering prefix stripped, `-` and `_` replaced with spaces, sentence-cased. `01-iceberg-bright-dark-line.png` becomes `Iceberg bright dark line`.
- `order` — `max + 10` within the collection, then `+10` per further file, sources sorted by path.
- `replacements` — `{}` for images. For `.html`, each `https://cdnjs…/<basename>` whose `<basename>` matches a `protected_files` basename is mapped to that vendor file's path relative to the destination.

Names are compared with the `.min` build marker dropped from both sides, so a page loading `chart.umd.js` picks up the vendored `chart.umd.min.js`. Same library, same API, different build. An exact name match instead leaves the entry with empty `replacements` and hands the user a `transform_html` failure to repair by hand, which is what `swe_bench_pro_by_lab.html` needed. Version numbers are already outside the match: the basename carries no version, so `Chart.js/4.4.1/` and `Chart.js/3.9.0/` both resolve to whichever build the repository vendors.

The cdnjs pre-fill keeps the second run from failing. `score-vs-output-tokens-per-task.html` references two vendored libraries; without pre-filled replacements the proposal succeeds and the re-run then dies in `transform_html`, moving the dead end rather than removing it.

The match is over raw text, not the `_parse_references` HTML parser, because `transform_html` replaces raw text: parsed attribute values are HTML-unescaped and would not always be found. To keep that match from being trusted blindly, the proposal applies its own replacements exactly as `transform_html` will and puts the result through the shared `has_cdnjs_reference` ban check. Anything still matching is a reference the pre-fill missed or a library that is not vendored at all, and the entry carries a warning in `ManifestProposal.warnings`, printed under the entry. The user learns before editing prose, not on the re-run.

### Proposal command behaviour

| Command | On unlisted sources |
| --- | --- |
| `plan` | Print the proposal. No write. |
| `apply` | Print, confirm once, write the manifest, stop. |
| `publish` | Print, confirm once, write the manifest, stop before creating a branch. |

All three exit `3`, meaning a manifest proposal is pending. Nothing is copied, committed, or pushed in the same run — the derived prose reaches the public catalogue only after the user has looked at it.

`publish` writes after its preflight has passed, and that preflight already accepts one unstaged `artefacts/manifest.json` edit. The edit-and-re-run cycle therefore needs no preflight change.

The serialized bytes are re-parsed with `manifest_from_bytes` before `_atomic_write` touches the file, so a proposal that cannot round-trip fails loudly and leaves the manifest untouched rather than broken.

## Orphan Cleanup

### Why manifest diffs miss files

Deletion derived from manifest diffs alone misses published files that neither manifest names. `create_sync_plan` proposes a deletion when an entry's source has disappeared, and when a destination in the `HEAD` manifest is absent from the working manifest. The published tree drifts out of both manifests whenever the diff window closes before the tree is fixed:

- A folder is renamed in `artefacts/` with `git mv` while the manifest still lists the old destination. The plan re-creates the old path as an addition and never mentions the new one.
- A `destination` edit is committed by some other route, so `HEAD` and the working manifest agree and the stale file matches neither.
- An entry is dropped from the manifest by hand in an earlier commit, leaving its file behind.

The condition is already detected, but too late and in the wrong place. `validate` walks `artefacts/`, subtracts the expected set, and raises `unexpected published file`. During `publish` that fires at step 4 — after the branch exists and `apply` has written the tree — and the repair is a manual `git rm`, which is what `chore: delete renamed chart` was.

### What the sweep does

Move that set into the plan. Files under `artefacts/` that the desired tree does not contain are proposed as deletions, previewed with everything else, and removed by `apply` under the same single confirmation.

Not in scope: rename detection (a renamed folder is one deletion and one addition, as a `destination` change already is; matching old files to new ones by content hash would make the preview shorter and its correctness unverifiable at the point of confirmation); deleting anything outside `artefacts/`; changing `validate`, which keeps rejecting the same set, now as a check on a tree the plan has already reconciled rather than as the only detector.

### The orphan set

An orphan is a file under `artefacts/` that is not in `desired_files`, not in `protected_files`, and not `.DS_Store`. `desired_files` already holds every entry destination plus `index.html` and `manifest.json`, so this is `validate`'s `unexpected` set computed against the plan's tree instead of the committed one. Both formulas live in one helper, so the plan cannot propose a tree that `validate` then rejects.

`.DS_Store` is ignored rather than deleted, matching the scan rules and `validate`'s `ignored_metadata` count. Directories are not sweep targets; `apply_plan` already prunes parents that its deletions emptied, which clears a renamed folder once its last file goes.

The sweep reads the tree, so it covers untracked files as well as tracked ones. That is deliberate: an untracked file under `artefacts/` fails `validate` on the pull request, so leaving it out of the preview would restore the dead end for a subset of cases.

### Preview

Orphans are listed under their own `Delete (orphaned)` heading, separate from the manifest-derived `Delete`. The two answer different questions — one says a source or entry went away, the other says the published tree holds something no manifest explains — and a user confirming a large deletion count needs to see which.

`Change.kind` gains `"orphan"` rather than overloading `"delete"`, so `format_plan` splits the groups without re-deriving the reason, and `apply_plan` treats both kinds identically.

### Sweep command behaviour

| Command | On orphans |
| --- | --- |
| `plan` | List them. No write. |
| `apply` | List, confirm once with the rest of the plan, delete. |
| `publish` | Same, inside the existing single confirmation, before the branch is created. |

No new exit code and no second confirmation. Unlike a manifest proposal, an orphan needs no user-authored content before the run can finish, so splitting it across two runs would only add a step.

`publish` reporting "already synchronized" now also requires the orphan list to be empty, otherwise a tree with nothing but orphans would exit clean and fail CI on the next unrelated change.

## Catalogue Generation

The existing `artefacts/index.html` remains the owner of its document structure and CSS. Two generated markers delimit the collection-card region. The sync command replaces only that region using the ordered collections and entries from the manifest.

The generated catalogue must link every manifest entry exactly once. Vendor files are runtime dependencies and are not catalogue entries. Generation escapes all manifest text before inserting it into HTML.

## Sync Flow

`plan` performs no repository writes. It compares the working manifest with the version in `HEAD` so metadata-only changes appear in the preview:

1. Resolve and validate the repository and source roots.
2. Parse and validate the manifest.
3. Scan approved source files and detect unlisted or missing entries. Unlisted sources print a manifest proposal and end the run with exit code 3.
4. Build the complete desired managed tree in a temporary directory.
5. Apply declared HTML replacements and generate the catalogue region.
6. Validate paths, hashes, catalogue coverage, and local references.
7. Print additions, updates, deletions, orphan deletions, unchanged files, and excluded file types.

`apply` runs the same plan, asks for confirmation, and then updates only destinations represented by the pre-apply manifest, approved new entries, and the generated catalogue region. Missing managed sources are valid deletion proposals. An invalid manifest blocks application. Unlisted approved source files stop the run after `apply` and `publish` have written the confirmed manifest proposal; `plan` prints the proposal and writes nothing.

`validate` checks the committed repository without requiring the local source directory. It validates the manifest schema, published path set, catalogue coverage, allowed extensions, HTML references, forbidden external runtime references, and homepage isolation.

## Publishing Flow

`publish` requires `git`, `gh`, `curl`, an authenticated GitHub CLI session, and an up-to-date local `main` branch. The worktree may be clean or contain one unstaged change to `artefacts/manifest.json`; staged changes and every other working-tree change are rejected. It performs this sequence:

1. Run `plan` and show the complete change set.
2. Ask for one explicit confirmation.
3. Create a timestamped branch such as `agent/sync-artefacts-20260726-143000`.
4. Run `apply` and the full local validation suite.
5. Commit only the manifest and generated artefact changes.
6. Push the branch and open a ready-for-review pull request to `main`.
7. Confirm the expected artefact-validation check exists, wait for all checks, and stop if any check fails or disappears.
8. Squash-merge the pull request only after all checks succeed.
9. Poll GitHub Pages until the merge commit is reported as built or a concrete failure is returned.
10. Request the homepage, catalogue, and every manifest URL over HTTPS and require HTTP 200.

The command prints the pull request URL, merge commit, catalogue URL, verified public URL count, and excluded file types.

If the plan contains no additions, updates, deletions, or orphan deletions, `publish` reports that the site is already synchronized and exits without creating a branch or pull request.

## Continuous Integration

`.github/workflows/validate-artefacts.yml` runs for pull requests that change the manifest, scripts, tests, workflow, or `artefacts/` tree. It runs unit tests and `python3 scripts/artefacts.py validate`.

The validation check is intentionally independent of `~/Downloads/Artefacts`. GitHub validates the committed public contract, while the local plan verifies source-to-destination byte identity before publication.

## Safety and Failure Handling

- The preview is calculated before mutation and lists every deletion separately.
- Deletion is limited to files under `artefacts/` that the validated desired tree does not contain, less protected files and ignored metadata. The catalogue shell, `.nojekyll`, and every file outside `artefacts/` are never deletion targets. Each deletion is an individual printed path; the script never recursively clears `artefacts/`. `apply_plan` refuses a target that is not a regular file or is a symbolic link.
- The desired tree is built and validated in a temporary directory before repository files change, so a manifest that fails to parse or validate stops the run with the tree untouched.
- The worst case for the orphan sweep is a manifest that legitimately holds no entries, which proposes deleting every published file. That case exists today through missing sources; the sweep does not widen it, and the preview shows every path before the confirmation.
- A local validation failure stops before commit or push.
- A push or PR-creation failure leaves the local branch and commit intact.
- A failed or missing GitHub check leaves the PR open and unmerged.
- A Pages failure reports the deployment error and skips public success claims.
- Public verification reports every non-200 URL and exits unsuccessfully.
- The existing root `index.html`, `styles.css`, and `script.js` are read-only boundaries for this workflow.

## Testing

Unit tests cover manifest validation, path normalization, source containment, extension filtering, duplicate detection, HTML replacement requirements, catalogue escaping, and diff classification.

Manifest proposals:

- Collection matched through an existing entry's source folder, not the folder name.
- New collection joins the section existing collections of the same media type use, even when that section has been renamed away from the constants; the constants apply only when the manifest has no example. `section_order` reused, `order` continues the section.
- `reconcile_inventory` carries the unlisted sources and the manifest on the error.
- Entry id collision suffixing.
- Title normalization, including the `NN-` prefix.
- Order continues from the collection maximum, stable across several new files.
- cdnjs replacement pre-fill, and no replacements for a `.html` file with no vendored reference.
- A cdnjs reference left unmapped after the pre-fill warns; a fully vendored file does not.

Orphan cleanup:

- A file under `artefacts/` matching no entry is proposed as an orphan deletion; a protected file and a `.DS_Store` are not.
- A renamed destination whose old path is still on disk and absent from both manifests is swept.
- The orphan set equals `validate`'s `unexpected` set for the same tree and manifest.
- `format_plan` separates orphan deletions from manifest-derived ones.

Integration tests use temporary source and repository directories to verify:

- add, update, and delete previews;
- byte-preserving image copies;
- HTML transformation;
- deterministic catalogue generation;
- protection of vendor and homepage files;
- refusal to apply an invalid or incomplete plan;
- `apply` on an unlisted source writes only the manifest, leaves `artefacts/` untouched, and exits `3`;
- the second `apply` run against the written manifest produces a normal add plan, and `plan` on an unlisted source writes nothing;
- `plan` on a tree with an orphan lists it and writes nothing;
- `apply` removes the orphan, prunes the emptied directory, and leaves every other published file byte-identical;
- a folder renamed with `git mv` and no manifest change resolves in one `apply`: the manifest destination is restored and the renamed copy removed;
- `publish` on a tree whose only difference is an orphan does not report "already synchronized";
- `validate` passes on the tree `apply` produced.

The pull-request workflow runs the complete test suite and repository validation. The publishing command then verifies the merged deployment rather than assuming a successful merge means the site is available.

## Out of Scope

- Background folder watchers or scheduled synchronization.
- Publishing without an explicit local confirmation.
- Uploading Markdown, Word, PDF, or other unapproved file types.
- Editing artefact content beyond declared HTML dependency replacements and deterministic trailing-whitespace removal.
- Automatically changing existing public paths when source files are renamed.
- Rename detection between deleted and added destinations.
