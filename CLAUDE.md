# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Two independent things live here:

1. **The personal profile site** — static HTML/CSS/JS at the repo root (`index.html`, `styles.css`, `script.js`, `privacy-policy.html`), served by GitHub Pages at https://kevinlin.github.io. No build step, no framework, no package manager. Edit the files and push.
2. **The artefact publishing pipeline** — `scripts/artefacts.py`, a stdlib-only Python CLI that syncs approved files from a private local directory (`~/Downloads/Artefacts`) into the public `artefacts/` tree, regenerates the catalogue page, and publishes through a PR.

## Commands

```bash
# Full test suite (only tests in the repo)
python3 -B -m unittest tests/test_artefacts.py

# A single test
python3 -B -m unittest tests.test_artefacts.ClassName.test_name -v

# Local consistency check of the published artefacts tree
python3 scripts/artefacts.py validate

# What CI runs on a PR (also enforces the homepage-untouched rule)
python3 scripts/artefacts.py validate --base-ref origin/main

# Artefact workflow: preview, apply locally, or publish end-to-end
python3 scripts/artefacts.py plan
python3 scripts/artefacts.py apply
python3 scripts/artefacts.py publish
```

`plan`/`apply`/`publish` accept `--repo` and `--source` overrides; both default to the repo root and `~/Downloads/Artefacts`.

There is no linter, formatter, or JS test runner. The site itself has no automated tests.

## Artefact pipeline architecture

`artefacts/manifest.json` is the source of truth for what is public. Anything not listed there is not published, and the local source directory is authoritative for *content* while the manifest is authoritative for *public URLs*. A renamed source file must keep its old `destination`, or the public URL breaks.

Flow inside `scripts/artefacts.py`:

- `scan_source` walks the source root; `apply_source_ignores` drops `ignored_sources` rules. Any remaining file with no manifest entry raises `UnlistedSourceError`, which `main` catches and turns into an interactive proposal (`propose_manifest_additions` → `merge_manifest_proposal`).
- `build_desired_files` renders the whole intended `artefacts/` tree in memory: byte copies for images, `transform_html` for `.html` (applies per-entry `replacements`, strips trailing spaces, hard-bans any cdnjs reference), `render_markdown_page` for `.md`.
- `create_sync_plan` diffs that tree against what is on disk and produces a `SyncPlan` of add/update/delete/orphan `Change`s. Nothing is written until the user confirms.
- `publish` runs preflight (clean tree, `gh auth`), branches, applies, runs the unit tests and `validate` locally, commits, opens a PR, waits for the `validate` check, squash-merges, waits for the GitHub Pages build, then fetches every public URL to confirm it is live. Any failure aborts.

Invariants worth knowing before changing this file:

- **Stdlib only.** No pip dependencies, ever. Third-party JS is vendored into `artefacts/vendor/` and listed under `protected_files`.
- **No cdnjs.** `has_cdnjs_reference` blocks publication of any page referencing it; there is no exemption, including for generated pages.
- **Markdown round-trips byte-exact.** `render_markdown_page` embeds the source verbatim in a `<script type="text/markdown">` block and `extract_markdown` is its exact inverse; the diff preview and `apply`'s byte verification both depend on this. Never apply trailing-whitespace stripping to Markdown (two trailing spaces are a hard line break).
- **`.html` and `.md` publish to a directory `index.html`** so public URLs carry no file extension.
- **`validate --base-ref` fails if `index.html`, `styles.css`, or `script.js` changed** (`HOMEPAGE_FILES`). An artefact sync PR must never touch the homepage; make site changes in a separate PR.
- Approved extensions: `.html`, `.md`, `.png`, `.jpeg`, `.jpg`, `.ico`. Word docs, PDFs, prompts, and analysis notes in the source directory stay private.

Design of record for this pipeline: [docs/specs/design_artefact-sync.md](docs/specs/design_artefact-sync.md). If code and that document disagree, the document wins — stop and ask.

## Site conventions

`PRODUCT.md` and `DESIGN.md` are binding, not decorative. `PRODUCT.md` defines the audience (recruiters scanning fast), the conversion goal, and the anti-references. `DESIGN.md` is the design system with named rules; `.impeccable/design.json` is its machine-readable form (OKLCH ramps, tokens). Read both before any visual change.

The rules that get broken most often:

- One brand blue (`#0063a3`) carries all structure and action. Signal Coral (`#ff5a5f`) appears on interaction only, never as a resting fill.
- Every `h2` opens a section with the 60×4px blue underline (`h2::after`). No uppercase tracked eyebrows.
- Inter is the only font family; hierarchy comes from weight and size.
- Light and dark themes are equals. The theme is read from `localStorage` before first paint to avoid a flash.
- Do not gate content visibility on a JS `.visible` class with `opacity: 0` as the default — reveals never fire under reduced-motion or headless rendering and the section ships blank.

`script.js` is plain DOM code initialised per feature (`initNavigation`, `initThemeToggle`, `initHeroNet`, `initSkillsGraph`, `initPhotoSlider`, …) from a single `DOMContentLoaded` handler. Projects are fetched live from the GitHub API and rendered by `generateProjectCard`.

`artefacts/index.html` is generated by `render_catalogue`; edit the generator rather than the file. It inlines its own favicon because the publish validator resolves local references against a tree containing only `artefacts/`.
