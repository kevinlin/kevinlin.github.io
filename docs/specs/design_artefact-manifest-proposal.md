# Artefact Manifest Proposal

Date: 2026-07-28

Extends [design_artefact-sync.md](design_artefact-sync.md). Read that first.

## Problem

An approved source file with no manifest entry aborts every command:

```text
Error: unlisted approved source files:
  fde/01-iceberg-bright-dark-line.png -> fde/01-iceberg-bright-dark-line.png
  llm-effort-level-vs-performance/score-vs-output-tokens-per-task.html -> llm-effort-level-vs-performance/score-vs-output-tokens-per-task/index.html
```

The suggestion covers only the destination. Every other field (id, title, collection, order, replacements) and any missing collection block must be hand-written before the command runs again. Adding a folder of images is a long manual edit against a schema the script already knows.

## Goal

Turn the block into a proposal. The script derives a complete, schema-valid manifest addition, shows it, and on confirmation writes it to `artefacts/manifest.json`. The user edits the placeholder prose and re-runs.

## Non-goals

- Publishing without a second, deliberate run.
- Deriving publication-quality titles or descriptions. Derived prose is a starting point the user is expected to edit.
- Changing `validate`, which never sees the local source directory.

## Error carries data

`UnlistedSourceError(InventoryError)` carries the `manifest` it was reconciled against and `unlisted: tuple[PurePosixPath, ...]`. `reconcile_inventory` raises it instead of the bare `InventoryError`, and its message is a bare summary line.

The old message spelled out each source and its suggested destination. That text is gone: `main()` catches `UnlistedSourceError` above the generic `ArtefactError` printer, so nothing would ever render it, and a second formatter for the same condition drifts from `format_proposal` unnoticed. The condition has one representation — data on the error, formatting in `format_proposal`.

Carrying the manifest keeps `handle_unlisted_sources` from re-reading and re-validating the file `create_sync_plan` loaded moments earlier. Subclassing still holds for anything catching `InventoryError`; only `main()` is aware of the new type.

## Derivation

One pure function:

```python
propose_manifest_additions(
    manifest: Manifest,
    unlisted: tuple[PurePosixPath, ...],
    source_root: Path,
) -> ManifestProposal          # .collections, .entries
```

It reads source files only to detect vendor references. It does not write.

### Collection match

A source's top-level folder maps to the collection used by existing entries whose `source` shares that folder. Folder-name-to-collection-id matching is wrong here: `llm-effort-level-vs-performance/` already maps to collection id `llm-performance`, and a naive match would invent a duplicate collection.

Unmatched folders produce a new collection:

- `id` — slugged folder name.
- `title` — folder name, title-cased.
- `description` — `TODO: describe this collection.`
- `section` — the section existing collections of the same media type already sit in. A folder with any `.html` source is a presentation collection, otherwise an image collection; existing collections are classified the same way, by the extension of their entries' destinations. Only when the manifest holds no collection of that media type do the `Presentations and analysis` / `Image collections` constants apply.
- `section_order` — reused from existing collections in that section; `max + 10` when the section itself is new.

Section names are manifest content, so they are learned rather than matched against the constants. Renaming a section in `manifest.json` is a content edit with no reason to touch Python; if the constants were authoritative, the next proposal would create a second section beside the renamed one under the stale name, splitting that media type across two catalogue headings.
- `order` — `max + 10` among collections in that section.

### Entry fields

- `source` is the scanned path; `destination` comes from the existing `suggest_destination`.
- `id` — slugged destination minus extension, `/` replaced with `-`; `-2`, `-3` … on collision with an existing or proposed id.
- `title` — source stem with a leading `NN-` ordering prefix stripped, `-` and `_` replaced with spaces, sentence-cased. `01-iceberg-bright-dark-line.png` becomes `Iceberg bright dark line`.
- `order` — `max + 10` within the collection, then `+10` per further file, sources sorted by path.
- `replacements` — `{}` for images. For `.html`, each `https://cdnjs…/<basename>` whose `<basename>` matches a `protected_files` basename is mapped to that vendor file's path relative to the destination.

The cdnjs pre-fill keeps the second run from failing. `score-vs-output-tokens-per-task.html` references two vendored libraries; without pre-filled replacements the proposal succeeds and the re-run then dies in `transform_html`, moving the dead end rather than removing it.

The match is over raw text, not the `_parse_references` HTML parser, because `transform_html` replaces raw text: parsed attribute values are HTML-unescaped and would not always be found. To keep that match from being trusted blindly, the proposal applies its own replacements exactly as `transform_html` will and puts the result through the shared `has_cdnjs_reference` ban check. Anything still matching is a reference the pre-fill missed or a library that is not vendored at all, and the entry carries a warning in `ManifestProposal.warnings`, printed under the entry. The user learns before editing prose, not on the re-run.

## Command behaviour

| Command | On unlisted sources |
| --- | --- |
| `plan` | Print the proposal. No write. |
| `apply` | Print, confirm once, write the manifest, stop. |
| `publish` | Print, confirm once, write the manifest, stop before creating a branch. |

All three exit `3`, meaning a manifest proposal is pending. Nothing is copied, committed, or pushed in the same run — the derived prose reaches the public catalogue only after the user has looked at it.

`publish` writes after its preflight has passed, and that preflight already accepts one unstaged `artefacts/manifest.json` edit. The edit-and-re-run cycle therefore needs no preflight change.

The serialized bytes are re-parsed with `manifest_from_bytes` before `_atomic_write` touches the file, so a proposal that cannot round-trip fails loudly and leaves the manifest untouched rather than broken.

## Testing

Unit:

- Collection matched through an existing entry's source folder, not the folder name.
- New collection joins the section existing collections of the same media type use, even when that section has been renamed away from the constants; the constants apply only when the manifest has no example. `section_order` reused, `order` continues the section.
- `reconcile_inventory` carries the unlisted sources and the manifest on the error.
- Entry id collision suffixing.
- Title normalization, including the `NN-` prefix.
- Order continues from the collection maximum, stable across several new files.
- cdnjs replacement pre-fill, and no replacements for a `.html` file with no vendored reference.
- A cdnjs reference left unmapped after the pre-fill warns; a fully vendored file does not.

Integration:

- `apply` on an unlisted source writes only the manifest, leaves `artefacts/` untouched, and exits `3`.
- The second `apply` run against the written manifest produces a normal add plan.
- `plan` writes nothing.

## Documentation

`design_artefact-sync.md` changes in two places: the "unlisted source blocks publication" rule under Source and Destination Rules, and step 3 of the Sync Flow.
