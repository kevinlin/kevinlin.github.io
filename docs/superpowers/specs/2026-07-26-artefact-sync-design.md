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

- Every approved source file must have one manifest entry. An unlisted source blocks publication and prints a suggested entry with a normalized destination.
- Every manifest source must exist. A missing source is shown as a deletion and, after confirmation, removes its entry and destination.
- Destination paths must be unique, relative, lowercase kebab-case, and contained below `artefacts/`.
- HTML presentations use a directory `index.html`; images retain an approved image extension.
- Binary files are copied byte-for-byte and verified with SHA-256.
- HTML replacements are exact manifest declarations. An expected replacement that is absent is an error.
- Published HTML must not contain forbidden cdnjs runtime references after transformation.
- Vendor files and the catalogue shell are protected and are never deleted by source mirroring.
- Files outside the allowlist are never copied.

Renaming a source does not require changing its public URL. Only the manifest's `source` field changes. Changing a `destination` is treated as an explicit public URL migration and appears as one deletion and one addition in the preview.

## Catalogue Generation

The existing `artefacts/index.html` remains the owner of its document structure and CSS. Two generated markers delimit the collection-card region. The sync command replaces only that region using the ordered collections and entries from the manifest.

The generated catalogue must link every manifest entry exactly once. Vendor files are runtime dependencies and are not catalogue entries. Generation escapes all manifest text before inserting it into HTML.

## Sync Flow

`plan` performs no repository writes:

1. Resolve and validate the repository and source roots.
2. Parse and validate the manifest.
3. Scan approved source files and detect unlisted or missing entries.
4. Build the complete desired managed tree in a temporary directory.
5. Apply declared HTML replacements and generate the catalogue region.
6. Validate paths, hashes, catalogue coverage, and local references.
7. Print additions, updates, deletions, unchanged files, and excluded file types.

`apply` runs the same plan, asks for confirmation, and then updates only destinations represented by the pre-apply manifest, approved new entries, and the generated catalogue region. Missing managed sources are valid deletion proposals. Unlisted approved source files or an invalid manifest block application.

`validate` checks the committed repository without requiring the local source directory. It validates the manifest schema, published path set, catalogue coverage, allowed extensions, HTML references, forbidden external runtime references, and homepage isolation.

## Publishing Flow

`publish` requires `git`, `gh`, `curl`, an authenticated GitHub CLI session, a clean worktree, and an up-to-date local `main` branch. It performs this sequence:

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

## Continuous Integration

`.github/workflows/validate-artefacts.yml` runs for pull requests that change the manifest, scripts, tests, workflow, or `artefacts/` tree. It runs unit tests and `python3 scripts/artefacts.py validate`.

The validation check is intentionally independent of `~/Downloads/Artefacts`. GitHub validates the committed public contract, while the local plan verifies source-to-destination byte identity before publication.

## Safety and Failure Handling

- The preview is calculated before mutation and lists every deletion separately.
- Deletion is limited to destinations represented by the pre-apply manifest. Protected files, the catalogue shell, `.nojekyll`, and unrelated repository files are never deletion targets. The script never recursively clears `artefacts/`.
- The desired tree is built and validated in a temporary directory before repository files change.
- A local validation failure stops before commit or push.
- A push or PR-creation failure leaves the local branch and commit intact.
- A failed or missing GitHub check leaves the PR open and unmerged.
- A Pages failure reports the deployment error and skips public success claims.
- Public verification reports every non-200 URL and exits unsuccessfully.
- The existing root `index.html`, `styles.css`, and `script.js` are read-only boundaries for this workflow.

## Testing

Unit tests cover manifest validation, path normalization, source containment, extension filtering, duplicate detection, HTML replacement requirements, catalogue escaping, and diff classification.

Integration tests use temporary source and repository directories to verify:

- add, update, and delete previews;
- byte-preserving image copies;
- HTML transformation;
- deterministic catalogue generation;
- protection of vendor and homepage files;
- refusal to apply an invalid or incomplete plan.

The pull-request workflow runs the complete test suite and repository validation. The publishing command then verifies the merged deployment rather than assuming a successful merge means the site is available.

## Out of Scope

- Background folder watchers or scheduled synchronization.
- Publishing without an explicit local confirmation.
- Uploading Markdown, Word, PDF, or other unapproved file types.
- Editing artefact content beyond declared HTML dependency replacements.
- Automatically changing existing public paths when source files are renamed.
