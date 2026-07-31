# Markdown Artefact Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `~/Downloads/Artefacts` Markdown files as browser-rendered pages in the `artefacts/` catalogue, and show a unified diff of every changed document before it is committed.

**Architecture:** `.md` joins the approved source extensions and follows the `.html` destination rule (one directory `index.html` per entry). `render_markdown_page` emits a self-contained HTML shell that embeds the Markdown verbatim inside a `<script type="text/markdown">` block and renders it client-side with a vendored `marked.min.js`. `extract_markdown` is the exact inverse of that embedding, and the diff preview uses it to recover the published Markdown and compare it with the new source.

**Tech Stack:** Python 3 standard library only (`re`, `difflib`, `html`, `json`, `pathlib`). One vendored third-party JavaScript file (`marked.min.js`). Tests are `unittest`.

## Global Constraints

- `scripts/artefacts.py` uses the Python standard library only. No pip installs, no new imports outside the stdlib.
- The design of record is `docs/specs/design_artefact-sync.md`, section `## Markdown Documents`. Where this plan and that document disagree, the design wins — stop and ask.
- Published HTML must contain no `cdnjs.cloudflare.com` or `https://cdnjs` reference. `has_cdnjs_reference` enforces this and there is no exemption for generated pages.
- Markdown content is preserved **byte-exact** through embedding and extraction. The trailing-whitespace stripping that `transform_html` applies must NOT be applied to Markdown content: two trailing spaces are a Markdown hard line break, and `apply`'s byte-verification plus the diff both depend on the round trip being lossless.
- Every entry has exactly one destination. Do not publish a `.md` file alongside its page.
- Markdown entries carry `replacements: {}`. The generated shell owns its own references.
- Run the full suite with `python3 -B -m unittest tests/test_artefacts.py` from the repository root. Individual tests: `python3 -B -m unittest tests.test_artefacts.ClassName.test_name -v`.
- Tests import the script through the `artefacts_cli` module alias already set up at the top of `tests/test_artefacts.py`. Reference everything as `artefacts_cli.<name>`.
- The rendered Markdown is inserted with `innerHTML` and is not sanitized. Every source is authored by the repository owner and reviewed in the plan preview before publication, so there is no untrusted input. Do not add a sanitizer, and do not publish third-party Markdown through this path.
- Work on branch `design/artefact-markdown`, which already holds the committed design. Commit after every task.

---

### Task 1: Vendor the Markdown parser

Fetch `marked.min.js` into `artefacts/vendor/` and register it as a protected file. Nothing renders without it, so it comes first.

**This task performs one network download.** Confirm with the user before running Step 1 if they have not already approved it.

**Files:**
- Create: `artefacts/vendor/marked.min.js`
- Modify: `artefacts/manifest.json` (the `protected_files` array)
- Test: none. This is a binary asset plus a one-line manifest edit; Task 2 onward exercise it.

**Interfaces:**
- Consumes: nothing.
- Produces: the protected path `vendor/marked.min.js`, looked up by name in Task 4. The browser global is `marked`, and the render call is `marked.parse(text)`.

- [ ] **Step 1: Resolve the latest version and download it pinned**

```bash
VERSION=$(curl -fsSL https://registry.npmjs.org/marked/latest \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])")
echo "marked version: $VERSION"
curl -fsSL "https://cdn.jsdelivr.net/npm/marked@${VERSION}/marked.min.js" \
  -o artefacts/vendor/marked.min.js
```

Pinning the version in the URL means the committed file is reproducible. Record `$VERSION` — it goes in the commit message.

- [ ] **Step 2: Verify the download is a real minified bundle**

```bash
ls -l artefacts/vendor/marked.min.js
head -c 200 artefacts/vendor/marked.min.js; echo
grep -c "cdnjs" artefacts/vendor/marked.min.js || true
```

Expected: a file over 30 KB, opening with minified JavaScript (not an HTML error page), and `0` cdnjs matches. If the file is small or starts with `<`, the download failed — stop.

- [ ] **Step 3: Confirm the global name and entry point**

```bash
grep -o "marked" artefacts/vendor/marked.min.js | head -1
grep -c "parse" artefacts/vendor/marked.min.js
```

Expected: `marked` present, `parse` present. The UMD bundle attaches `marked` to `window` and exposes `marked.parse`.

- [ ] **Step 4: Register it as a protected file**

Add `"vendor/marked.min.js"` to the `protected_files` array in `artefacts/manifest.json`, keeping the existing entries. The array becomes:

```json
  "protected_files": [
    "vendor/chart.umd.min.js",
    "vendor/chartjs-plugin-datalabels.min.js",
    "vendor/marked.min.js"
  ],
```

Protected files are runtime dependencies: the orphan sweep never deletes them and the catalogue never links them.

- [ ] **Step 5: Verify the repository still validates**

Run: `python3 scripts/artefacts.py validate`
Expected: exits 0 and prints `Validated N manifest entries and M local links.` A `missing protected file` error means the download or the path is wrong.

- [ ] **Step 6: Run the full suite to confirm nothing regressed**

Run: `python3 -B -m unittest tests/test_artefacts.py`
Expected: OK, no failures.

- [ ] **Step 7: Commit**

```bash
git add artefacts/vendor/marked.min.js artefacts/manifest.json
git commit -m "feat: vendor marked.min.js for markdown artefact rendering

Pinned download of marked <VERSION> from jsdelivr, registered in
protected_files so the orphan sweep leaves it alone and published
pages can reference it by relative path instead of a CDN."
```

Replace `<VERSION>` with the value recorded in Step 1.

---

### Task 2: Accept `.md` as an approved source

Three small changes let a `.md` source reach the manifest: the extension allowlist, the destination rule, and the destination suggestion.

**Files:**
- Modify: `scripts/artefacts.py:22` (`APPROVED_EXTENSIONS`)
- Modify: `scripts/artefacts.py:334-349` (the destination-suffix checks in `validate_manifest`)
- Modify: `scripts/artefacts.py:464-470` (`suggest_destination`)
- Test: `tests/test_artefacts.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `DIRECTORY_INDEX_EXTENSIONS = frozenset({".html", ".md"})`, used by Task 5 to decide which sources get a generated page. `suggest_destination(PurePosixPath("Notes/Report.md"))` returns `PurePosixPath("notes/report/index.html")`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_artefacts.py`, at the end of `class ManifestTests` (immediately before `class NormalizeOrdersTests` at line 180):

```python
    def test_accepts_markdown_source_with_directory_index_destination(self):
        payload = valid_payload()
        payload["entries"][0]["source"] = "Notes/Report.md"
        payload["entries"][0]["destination"] = "notes/report/index.html"
        manifest = artefacts_cli.load_manifest(self.write_manifest(payload))
        self.assertEqual(
            manifest.entries[0].destination, PurePosixPath("notes/report/index.html")
        )

    def test_rejects_markdown_destination_that_keeps_the_md_extension(self):
        payload = valid_payload()
        payload["entries"][0]["source"] = "Notes/Report.md"
        payload["entries"][0]["destination"] = "notes/report.md"
        self.assert_manifest_error(payload, "must end in index.html")
```

Add to `tests/test_artefacts.py`, at the end of `class SourceInventoryTests` (immediately before `class ManifestProposalTests` at line 349):

```python
    def test_scan_reports_markdown_as_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Notes").mkdir()
            (root / "Notes" / "Report.md").write_text("# Report\n", encoding="utf-8")
            (root / "Notes" / "Draft.docx").write_bytes(b"binary")
            inventory = artefacts_cli.scan_source(root)
        self.assertEqual(inventory.approved, (PurePosixPath("Notes/Report.md"),))
        self.assertEqual(inventory.excluded_suffixes, (".docx",))

    def test_suggest_destination_maps_markdown_to_a_directory_index(self):
        self.assertEqual(
            artefacts_cli.suggest_destination(PurePosixPath("Notes/My_Report.md")),
            PurePosixPath("notes/my-report/index.html"),
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python3 -B -m unittest \
  tests.test_artefacts.ManifestTests.test_accepts_markdown_source_with_directory_index_destination \
  tests.test_artefacts.ManifestTests.test_rejects_markdown_destination_that_keeps_the_md_extension \
  tests.test_artefacts.SourceInventoryTests.test_scan_reports_markdown_as_approved \
  tests.test_artefacts.SourceInventoryTests.test_suggest_destination_maps_markdown_to_a_directory_index -v
```

Expected: 4 failures. The manifest tests fail with `unsupported source extension`, the scan test reports `.md` as an excluded suffix, and the destination test returns `notes/my-report.md`.

- [ ] **Step 3: Add the extension and the shared set**

In `scripts/artefacts.py`, replace line 22:

```python
APPROVED_EXTENSIONS = frozenset({".html", ".png", ".jpeg", ".jpg", ".ico"})
```

with:

```python
# Sources that become a generated page rather than a byte copy. Both publish to a
# directory index.html so the public URL carries no file extension.
DIRECTORY_INDEX_EXTENSIONS = frozenset({".html", ".md"})
APPROVED_EXTENSIONS = frozenset({".html", ".md", ".png", ".jpeg", ".jpg", ".ico"})
```

- [ ] **Step 4: Widen the destination rule**

In `validate_manifest`, replace the block at lines 341-349:

```python
        if source_suffix == ".html":
            if entry.destination.name != "index.html":
                raise ManifestError(
                    f"HTML destination for entry {entry.id} must end in index.html"
                )
        elif destination_suffix != source_suffix:
```

with:

```python
        if source_suffix in DIRECTORY_INDEX_EXTENSIONS:
            if entry.destination.name != "index.html":
                raise ManifestError(
                    f"generated destination for entry {entry.id} must end in index.html"
                )
        elif destination_suffix != source_suffix:
```

- [ ] **Step 5: Widen the destination suggestion**

In `suggest_destination`, replace line 468:

```python
    if suffix == ".html":
```

with:

```python
    if suffix in DIRECTORY_INDEX_EXTENSIONS:
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
python3 -B -m unittest \
  tests.test_artefacts.ManifestTests \
  tests.test_artefacts.SourceInventoryTests -v
```

Expected: PASS. The pre-existing `test_rejects_html_destination_without_directory_index` asserts on the message text — if it fails, it is matching `HTML destination`; update its expected pattern to `must end in index.html`, which covers both source types.

- [ ] **Step 7: Run the full suite**

Run: `python3 -B -m unittest tests/test_artefacts.py`
Expected: OK.

- [ ] **Step 8: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py
git commit -m "feat: accept markdown sources in the artefact manifest

A .md source follows the .html destination rule: one directory
index.html per entry, so the public URL carries no extension.
DIRECTORY_INDEX_EXTENSIONS names the two source types that become a
generated page rather than a byte copy."
```

---

### Task 3: Embed and extract Markdown losslessly

The script-block escaping and its exact inverse. This is the correctness core: if the round trip is not lossless, `apply`'s byte-verification and the Task 6 diff are both wrong.

**Files:**
- Modify: `scripts/artefacts.py` (add after `TRAILING_SPACE` at line 454)
- Test: `tests/test_artefacts.py` (new test class)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `escape_markdown_block(text: str) -> str`
  - `unescape_markdown_block(text: str) -> str`
  - `extract_markdown(document: str) -> str | None` — returns the embedded Markdown, or `None` when the document has no Markdown block. Task 4 embeds with `escape_markdown_block`; Task 6 recovers with `extract_markdown`.
  - `MARKDOWN_BLOCK_START` / `MARKDOWN_BLOCK_END` marker constants.

- [ ] **Step 1: Write the failing tests**

Add a new class to `tests/test_artefacts.py`, immediately before `class CatalogueTests` (line 672):

```python
class MarkdownEscapingTests(unittest.TestCase):
    def round_trip(self, text: str) -> str:
        return artefacts_cli.unescape_markdown_block(
            artefacts_cli.escape_markdown_block(text)
        )

    def test_escapes_a_script_terminator(self):
        escaped = artefacts_cli.escape_markdown_block("text </script> more")
        self.assertNotIn("</script", escaped)
        self.assertIn("<\\/script", escaped)

    def test_escapes_a_comment_opener(self):
        escaped = artefacts_cli.escape_markdown_block("text <!-- hidden --> more")
        self.assertNotIn("<!--", escaped)
        self.assertIn("<\\!--", escaped)

    def test_escape_is_case_insensitive_on_the_tag_name(self):
        escaped = artefacts_cli.escape_markdown_block("</SCRIPT>")
        self.assertNotIn("</SCRIPT", escaped)
        self.assertIn("<\\/SCRIPT", escaped)

    def test_round_trip_preserves_plain_markdown_exactly(self):
        text = "# Title\n\nBody with two trailing spaces  \nnext line\n\n- a\n- b\n"
        self.assertEqual(self.round_trip(text), text)

    def test_round_trip_preserves_a_preexisting_escaped_marker(self):
        # A source that already contains the escaped form must not be corrupted:
        # escaping adds a backslash, unescaping removes exactly one.
        text = "literal <\\/script and <\\!-- in the source\n"
        self.assertEqual(self.round_trip(text), text)

    def test_round_trip_preserves_markers_inside_a_code_fence(self):
        text = "```html\n<script>alert(1)</script>\n<!-- note -->\n```\n"
        self.assertEqual(self.round_trip(text), text)

    def test_extract_markdown_recovers_the_embedded_source(self):
        text = "# Title\n\nSee </script> and <!-- this -->.\n"
        document = (
            "<body>\n"
            + artefacts_cli.MARKDOWN_BLOCK_START
            + artefacts_cli.escape_markdown_block(text)
            + artefacts_cli.MARKDOWN_BLOCK_END
            + "\n</body>\n"
        )
        self.assertEqual(artefacts_cli.extract_markdown(document), text)

    def test_extract_markdown_returns_none_without_a_block(self):
        self.assertIsNone(artefacts_cli.extract_markdown("<html>no block</html>"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -B -m unittest tests.test_artefacts.MarkdownEscapingTests -v`
Expected: 8 errors, all `AttributeError: module 'artefacts_cli' has no attribute 'escape_markdown_block'` or similar.

- [ ] **Step 3: Implement the escaping pair and the extractor**

In `scripts/artefacts.py`, add after `TRAILING_SPACE = re.compile(...)` (line 454):

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -B -m unittest tests.test_artefacts.MarkdownEscapingTests -v`
Expected: 8 tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `python3 -B -m unittest tests/test_artefacts.py`
Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py
git commit -m "feat: lossless markdown script-block escaping

Escape </script and <!-- by inserting a backslash after the <. The
pattern also matches the already-escaped form, so escape adds one
backslash and unescape removes one, which keeps the round trip exact
even when the source contains the escaped sequence verbatim.

extract_markdown is that inverse plus block-marker lookup, and it is
what the diff preview compares against."
```

---

### Task 4: Render the Markdown page

The self-contained shell. Deterministic output, references only the vendored parser, and carries the Markdown verbatim.

**Files:**
- Modify: `scripts/artefacts.py` (add after `extract_markdown` from Task 3)
- Test: `tests/test_artefacts.py` (new test class)

**Interfaces:**
- Consumes: `escape_markdown_block`, `MARKDOWN_BLOCK_START`, `MARKDOWN_BLOCK_END`, `extract_markdown` (Task 3); the protected path `vendor/marked.min.js` (Task 1).
- Produces:
  - `MARKDOWN_VENDOR_NAME = "marked.min.js"`
  - `render_markdown_page(entry: Entry, source_bytes: bytes, vendor_path: PurePosixPath) -> bytes` — raises `TransformationError` when the source is not UTF-8. Task 5 calls it from `build_desired_files`.
  - `markdown_vendor_path(manifest: Manifest) -> PurePosixPath` — the protected file whose basename is `marked.min.js`; raises `TransformationError` when it is not registered.

- [ ] **Step 1: Write the failing tests**

Add a new class to `tests/test_artefacts.py`, immediately after `class MarkdownEscapingTests`:

```python
def markdown_entry(destination: str = "notes/report/index.html") -> "artefacts_cli.Entry":
    return artefacts_cli.Entry(
        id="report",
        source=PurePosixPath("Notes/Report.md"),
        destination=PurePosixPath(destination),
        title="Report & Analysis",
        collection="notes",
        order=10,
        replacements={},
    )


class MarkdownRenderTests(unittest.TestCase):
    VENDOR = PurePosixPath("vendor/marked.min.js")

    def render(self, text: str, destination: str = "notes/report/index.html") -> str:
        return artefacts_cli.render_markdown_page(
            markdown_entry(destination), text.encode("utf-8"), self.VENDOR
        ).decode("utf-8")

    def test_page_embeds_the_source_and_extraction_recovers_it(self):
        text = "# Report\n\nBody with </script> and <!-- note -->.\n"
        page = self.render(text)
        self.assertEqual(artefacts_cli.extract_markdown(page), text)

    def test_page_escapes_the_title_and_uses_it_verbatim(self):
        page = self.render("# Report\n")
        self.assertIn("<title>Report &amp; Analysis</title>", page)
        self.assertIn("<h1>Report &amp; Analysis</h1>", page)

    def test_vendor_reference_depth_follows_the_destination(self):
        two_deep = self.render("# R\n", "notes/report/index.html")
        self.assertIn('src="../../vendor/marked.min.js"', two_deep)
        three_deep = self.render("# R\n", "notes/prompts/report/index.html")
        self.assertIn('src="../../../vendor/marked.min.js"', three_deep)

    def test_back_link_points_at_the_catalogue(self):
        page = self.render("# R\n", "notes/report/index.html")
        self.assertIn('href="../../"', page)

    def test_page_has_no_cdnjs_reference(self):
        self.assertFalse(
            artefacts_cli.has_cdnjs_reference(self.render("# R\n"))
        )

    def test_render_is_deterministic(self):
        text = "# Report\n\nBody.\n"
        self.assertEqual(self.render(text), self.render(text))

    def test_render_rejects_a_non_utf8_source(self):
        with self.assertRaisesRegex(
            artefacts_cli.TransformationError, "not UTF-8"
        ):
            artefacts_cli.render_markdown_page(
                markdown_entry(), b"\xff\xfe not utf-8", self.VENDOR
            )

    def test_page_ends_with_a_newline(self):
        self.assertTrue(self.render("# R\n").endswith("\n"))

    def test_vendor_lookup_finds_the_registered_parser(self):
        manifest = artefacts_cli.Manifest(
            version=1,
            protected_files=(
                PurePosixPath("vendor/chart.umd.min.js"),
                PurePosixPath("vendor/marked.min.js"),
            ),
            collections=(),
            entries=(),
        )
        self.assertEqual(artefacts_cli.markdown_vendor_path(manifest), self.VENDOR)

    def test_vendor_lookup_reports_an_unregistered_parser(self):
        manifest = artefacts_cli.Manifest(
            version=1,
            protected_files=(PurePosixPath("vendor/chart.umd.min.js"),),
            collections=(),
            entries=(),
        )
        with self.assertRaisesRegex(
            artefacts_cli.TransformationError, "marked.min.js"
        ):
            artefacts_cli.markdown_vendor_path(manifest)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -B -m unittest tests.test_artefacts.MarkdownRenderTests -v`
Expected: 10 errors, `module 'artefacts_cli' has no attribute 'render_markdown_page'`.

- [ ] **Step 3: Implement the template and the renderer**

In `scripts/artefacts.py`, add after `extract_markdown`:

```python
MARKDOWN_VENDOR_NAME = "marked.min.js"

# One self-contained document per Markdown entry, matching artefacts/index.html:
# same colour tokens and fonts, same pre-paint theme script, a prose column, and a
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
            width: min(760px, calc(100% - 48px));
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
            document.getElementById('markdown-body').innerHTML = marked.parse(text);
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
```

Note on the template: it is a `str.format` template, so every literal `{` and `}` in the CSS and JavaScript is doubled. The JavaScript regex needs four backslashes in the Python source to emit two in the output (`/<(\\+)(\/script|!--)/gi`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -B -m unittest tests.test_artefacts.MarkdownRenderTests -v`
Expected: 10 tests PASS. If `test_page_embeds_the_source_and_extraction_recovers_it` fails, the `{`/`}` doubling in the template is wrong — a `KeyError` from `format` points at the exact unescaped brace.

- [ ] **Step 5: Eyeball a rendered page in a browser**

```bash
python3 - <<'PY'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("a", "scripts/artefacts.py")
a = importlib.util.module_from_spec(spec); sys.modules["a"] = a
spec.loader.exec_module(a)
out = pathlib.Path("/tmp/mdcheck/artefacts/notes/report")
out.mkdir(parents=True, exist_ok=True)
(pathlib.Path("/tmp/mdcheck/artefacts/vendor")).mkdir(parents=True, exist_ok=True)
(pathlib.Path("/tmp/mdcheck/artefacts/vendor/marked.min.js")).write_bytes(
    pathlib.Path("artefacts/vendor/marked.min.js").read_bytes())
entry = a.Entry(id="r", source=pathlib.PurePosixPath("Notes/Report.md"),
                destination=pathlib.PurePosixPath("notes/report/index.html"),
                title="Sample report", collection="notes", order=10, replacements={})
md = pathlib.Path("~/Downloads/Artefacts/fde/analysis.md").expanduser().read_bytes()
(out / "index.html").write_bytes(
    a.render_markdown_page(entry, md, pathlib.PurePosixPath("vendor/marked.min.js")))
print(out / "index.html")
PY
open /tmp/mdcheck/artefacts/notes/report/index.html
```

Expected: headings, lists, tables, and code blocks render; the theme toggle in the OS dark mode shows dark colours; the back-link points at the (absent) catalogue directory. Confirm the browser console is clean. Clean up with `rm -rf /tmp/mdcheck` when done.

- [ ] **Step 6: Run the full suite**

Run: `python3 -B -m unittest tests/test_artefacts.py`
Expected: OK.

- [ ] **Step 7: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py
git commit -m "feat: render markdown artefacts as self-contained pages

render_markdown_page emits one HTML document per entry: inline CSS
matching the catalogue, the Markdown embedded verbatim in a
text/markdown script block, and a relative reference to the vendored
marked.min.js. The vendor prefix follows the destination depth, so a
nested document resolves the parser correctly."
```

---

### Task 5: Build Markdown destinations in the desired tree

Wire the renderer into the plan's tree construction, and stop the proposal from deriving HTML-only fields for a `.md` entry.

**Files:**
- Modify: `scripts/artefacts.py:744-767` (`build_desired_files`)
- Test: `tests/test_artefacts.py` (`DesiredTreeTests`)

**Interfaces:**
- Consumes: `render_markdown_page`, `markdown_vendor_path` (Task 4); `DIRECTORY_INDEX_EXTENSIONS` (Task 2).
- Produces: `build_desired_files` now returns a rendered page for every `.md` entry. No signature change, so `create_sync_plan` needs no edit in this task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_artefacts.py`, at the end of `class DesiredTreeTests` (immediately before `class CatalogueTests`):

```python
    def test_build_desired_files_renders_markdown_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Notes").mkdir()
            (root / "Notes" / "Report.md").write_text(
                "# Report\n\nBody.\n", encoding="utf-8"
            )
            manifest = artefacts_cli.Manifest(
                version=1,
                protected_files=(PurePosixPath("vendor/marked.min.js"),),
                collections=(),
                entries=(markdown_entry(),),
            )
            desired = artefacts_cli.build_desired_files(manifest, root)
        page = desired[PurePosixPath("notes/report/index.html")].decode("utf-8")
        self.assertEqual(artefacts_cli.extract_markdown(page), "# Report\n\nBody.\n")
        self.assertIn('src="../../vendor/marked.min.js"', page)

    def test_build_desired_files_requires_the_vendored_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Notes").mkdir()
            (root / "Notes" / "Report.md").write_text("# R\n", encoding="utf-8")
            manifest = artefacts_cli.Manifest(
                version=1,
                protected_files=(),
                collections=(),
                entries=(markdown_entry(),),
            )
            with self.assertRaisesRegex(
                artefacts_cli.TransformationError, "marked.min.js"
            ):
                artefacts_cli.build_desired_files(manifest, root)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python3 -B -m unittest \
  tests.test_artefacts.DesiredTreeTests.test_build_desired_files_renders_markdown_entries \
  tests.test_artefacts.DesiredTreeTests.test_build_desired_files_requires_the_vendored_parser -v
```

Expected: both fail because the `.md` source is copied byte-for-byte rather than rendered, so `extract_markdown` returns `None` and the vendor reference is absent.

- [ ] **Step 3: Dispatch on the source suffix in `build_desired_files`**

Replace lines 761-766:

```python
        source_bytes = source_path.read_bytes()
        if entry.source.suffix.lower() == ".html":
            output = transform_html(entry, source_bytes)
        else:
            output = source_bytes
        desired[entry.destination] = output
```

with:

```python
        source_bytes = source_path.read_bytes()
        suffix = entry.source.suffix.lower()
        if suffix == ".html":
            output = transform_html(entry, source_bytes)
        elif suffix == ".md":
            output = render_markdown_page(
                entry, source_bytes, markdown_vendor_path(manifest)
            )
        else:
            output = source_bytes
        desired[entry.destination] = output
```

`markdown_vendor_path` is looked up inside the loop on purpose. Hoisting it above the loop would make a manifest with no Markdown entries fail when the parser is not vendored.

Note what does **not** change here: `propose_manifest_additions` already gates the replacement pre-fill on `== ".html"` and falls through to `replacements = {}` for everything else, so a `.md` source already gets no replacements and no cdnjs warning. Task 7 restructures that block for the title rule; leave it alone until then.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -B -m unittest tests.test_artefacts.DesiredTreeTests -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python3 -B -m unittest tests/test_artefacts.py`
Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py
git commit -m "feat: build markdown destinations in the desired tree

build_desired_files dispatches on the source suffix and renders a .md
entry into its page. The vendored parser is looked up per markdown
entry, so a manifest without markdown never requires it."
```

---

### Task 6: Diff every changed Markdown document

The preview gains a unified diff per updated Markdown entry, so a public commit is confirmed against the prose that changed.

**Files:**
- Modify: `scripts/artefacts.py:149-157` (`SyncPlan`)
- Modify: `scripts/artefacts.py:978-1059` (`create_sync_plan`)
- Modify: `scripts/artefacts.py:1082-1106` (`format_plan`)
- Modify: `scripts/artefacts.py:12-19` (add `difflib` to the imports)
- Test: `tests/test_artefacts.py` (new class plus `ApplyTests`)

**Interfaces:**
- Consumes: `extract_markdown` (Task 3).
- Produces:
  - `MARKDOWN_DIFF_LINE_LIMIT = 40`
  - `markdown_diff(published: bytes | None, source: bytes, limit: int = MARKDOWN_DIFF_LINE_LIMIT) -> str`
  - `SyncPlan.markdown_diffs: tuple[tuple[PurePosixPath, str], ...]` — appended as the last field, defaulting to `()` so existing constructions keep working.
  - `format_plan` emits a `Markdown changes (N)` block.

- [ ] **Step 1: Write the failing tests**

Add a new class to `tests/test_artefacts.py`, immediately after `class MarkdownRenderTests`:

```python
class MarkdownDiffTests(unittest.TestCase):
    def page(self, text: str) -> bytes:
        return artefacts_cli.render_markdown_page(
            markdown_entry(), text.encode("utf-8"), PurePosixPath("vendor/marked.min.js")
        )

    def test_diff_reports_changed_lines_only(self):
        diff = artefacts_cli.markdown_diff(
            self.page("# Title\n\nOld body.\n"), b"# Title\n\nNew body.\n"
        )
        self.assertIn("-Old body.", diff)
        self.assertIn("+New body.", diff)
        self.assertNotIn("-# Title", diff)

    def test_identical_markdown_produces_no_diff(self):
        text = "# Title\n\nBody.\n"
        self.assertEqual(artefacts_cli.markdown_diff(self.page(text), text.encode()), "")

    def test_diff_is_truncated_with_the_remaining_count(self):
        old = "# Title\n\n" + "".join(f"old line {n}\n" for n in range(60))
        new = "# Title\n\n" + "".join(f"new line {n}\n" for n in range(60))
        diff = artefacts_cli.markdown_diff(self.page(old), new.encode("utf-8"), limit=10)
        lines = diff.splitlines()
        self.assertEqual(len(lines), 11)
        self.assertRegex(lines[-1], r"^… truncated, \d+ more lines$")

    def test_unextractable_page_reports_the_diff_as_unavailable(self):
        diff = artefacts_cli.markdown_diff(b"<html>hand written</html>", b"# New\n")
        self.assertIn("diff unavailable", diff)

    def test_missing_published_page_produces_no_diff(self):
        self.assertEqual(artefacts_cli.markdown_diff(None, b"# New\n"), "")
```

Add to `tests/test_artefacts.py`, at the end of `class ApplyTests`:

```python
    def test_plan_diffs_a_changed_markdown_document(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        artefacts = repo / "artefacts"
        (artefacts / "vendor" / "marked.min.js").write_bytes(b"/* parser */")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["protected_files"].append("vendor/marked.min.js")
        payload["entries"].append(
            {
                "id": "notes",
                "source": "Notes/Report.md",
                "destination": "charts/report/index.html",
                "title": "Report",
                "collection": "charts",
                "order": 40,
                "replacements": {},
            }
        )
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (source / "Notes").mkdir()
        (source / "Notes" / "Report.md").write_text(
            "# Report\n\nSecond version.\n", encoding="utf-8"
        )
        published = artefacts / "charts" / "report"
        published.mkdir(parents=True)
        (published / "index.html").write_bytes(
            artefacts_cli.render_markdown_page(
                artefacts_cli.Entry(
                    id="notes",
                    source=PurePosixPath("Notes/Report.md"),
                    destination=PurePosixPath("charts/report/index.html"),
                    title="Report",
                    collection="charts",
                    order=40,
                    replacements={},
                ),
                b"# Report\n\nFirst version.\n",
                PurePosixPath("vendor/marked.min.js"),
            )
        )
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, artefacts, manifest_path.read_bytes()
        )
        diffs = dict(plan.markdown_diffs)
        self.assertIn(PurePosixPath("charts/report/index.html"), diffs)
        body = diffs[PurePosixPath("charts/report/index.html")]
        self.assertIn("-First version.", body)
        self.assertIn("+Second version.", body)
        rendered = artefacts_cli.format_plan(plan)
        self.assertIn("Markdown changes (1)", rendered)
        self.assertIn("charts/report/index.html", rendered)

    def test_plan_without_markdown_reports_no_markdown_changes(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )
        self.assertEqual(plan.markdown_diffs, ())
        self.assertNotIn("Markdown changes", artefacts_cli.format_plan(plan))
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python3 -B -m unittest tests.test_artefacts.MarkdownDiffTests \
  tests.test_artefacts.ApplyTests.test_plan_diffs_a_changed_markdown_document \
  tests.test_artefacts.ApplyTests.test_plan_without_markdown_reports_no_markdown_changes -v
```

Expected: `MarkdownDiffTests` errors on the missing `markdown_diff`; the `ApplyTests` cases fail on `SyncPlan` having no `markdown_diffs` attribute.

- [ ] **Step 3: Import difflib**

In `scripts/artefacts.py`, add `import difflib` to the import block, keeping alphabetical order — between `from datetime import date, datetime` and `import html`.

- [ ] **Step 4: Implement the diff**

Add after `render_markdown_page`:

```python
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
```

- [ ] **Step 5: Carry the diffs on the plan**

Add the field to `SyncPlan` (line 149), last so existing positional constructions keep working:

```python
@dataclass(frozen=True)
class SyncPlan:
    manifest: Manifest
    next_manifest: Manifest
    desired_files: dict[PurePosixPath, bytes]
    changes: tuple[Change, ...]
    unchanged: tuple[PurePosixPath, ...]
    excluded_suffixes: tuple[str, ...]
    markdown_diffs: tuple[tuple[PurePosixPath, str], ...] = ()
```

In `create_sync_plan`, insert immediately before the `return SyncPlan(` statement:

```python
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
```

Then add the field to the returned `SyncPlan`:

```python
        excluded_suffixes=inventory.excluded_suffixes,
        markdown_diffs=tuple(markdown_diffs),
    )
```

Only `update` destinations are diffed. An `add` has nothing to compare against, and a `delete` or `orphan` has no source left to read.

- [ ] **Step 6: Print the diffs**

In `format_plan`, insert immediately after the `renumbered` block and before the `Unchanged` line:

```python
    if plan.markdown_diffs:
        lines.append(f"Markdown changes ({len(plan.markdown_diffs)})")
        for destination, body in plan.markdown_diffs:
            lines.append(f"  ~ {destination.as_posix()}")
            lines.extend(f"      {line}" for line in body.splitlines())
```

The heading is omitted entirely when there is nothing to show, matching how `Renumbered order` behaves.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
python3 -B -m unittest tests.test_artefacts.MarkdownDiffTests \
  tests.test_artefacts.ApplyTests -v
```

Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `python3 -B -m unittest tests/test_artefacts.py`
Expected: OK. `test_format_plan_lists_each_change_kind_and_excluded_types` asserts on `format_plan` output — if it fails, it is because of an ordering assumption; the `Markdown changes` block goes between `Renumbered order` and `Unchanged`, so adjust the assertion rather than the code.

- [ ] **Step 9: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py
git commit -m "feat: diff changed markdown documents in the plan preview

Every markdown entry classified as an update is diffed against the
markdown extracted from its published page, and format_plan prints
the result under Markdown changes. The published file is the same
basis the byte comparison uses, so the diff cannot disagree with the
change classification and no git call is needed.

Diffs are capped at 40 lines per entry with the remaining count
stated, because a silently truncated diff reads as a complete one."
```

---

### Task 7: Propose Markdown titles from the first heading

`AI_Education_Catalogue.md` should propose `AI Education Catalogue`, not `Ai education catalogue`.

**Files:**
- Modify: `scripts/artefacts.py:488-490` (`_derive_title`) and `scripts/artefacts.py:635-663` (the entry loop in `propose_manifest_additions`)
- Test: `tests/test_artefacts.py` (`ManifestProposalTests`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `markdown_title(source_path: Path) -> str | None` — the first ATX H1 with its `# ` stripped, or `None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_artefacts.py`, at the end of `class ManifestProposalTests`:

```python
    def proposal_for(self, name: str, body: str):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "Notes").mkdir()
        (root / "Notes" / name).write_text(body, encoding="utf-8")
        manifest = artefacts_cli.manifest_from_dict(valid_payload())
        artefacts_cli.validate_manifest(manifest)
        return artefacts_cli.propose_manifest_additions(
            manifest, (PurePosixPath(f"Notes/{name}"),), root
        )

    def test_markdown_title_comes_from_the_first_heading(self):
        proposal = self.proposal_for(
            "AI_Education_Catalogue.md", "# AI Education Catalogue\n\nBody.\n"
        )
        self.assertEqual(proposal.entries[0].title, "AI Education Catalogue")

    def test_markdown_title_falls_back_to_the_stem(self):
        proposal = self.proposal_for("01-my_report.md", "Body with no heading.\n")
        self.assertEqual(proposal.entries[0].title, "My report")

    def test_markdown_title_ignores_a_deeper_heading(self):
        proposal = self.proposal_for("my_report.md", "## Section\n\n# Real Title\n")
        self.assertEqual(proposal.entries[0].title, "Real Title")

    def test_markdown_proposal_gets_no_replacements_or_warning(self):
        # A cdnjs URL in Markdown prose is content, not a dependency to vendor.
        proposal = self.proposal_for(
            "my_report.md",
            "# Report\n\nhttps://cdnjs.cloudflare.com/ajax/libs/x/x.js\n",
        )
        self.assertEqual(proposal.entries[0].replacements, {})
        self.assertEqual(proposal.warnings, {})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -B -m unittest tests.test_artefacts.ManifestProposalTests -v`
Expected: `test_markdown_title_comes_from_the_first_heading` fails with `'Ai education catalogue' != 'AI Education Catalogue'`; `test_markdown_title_ignores_a_deeper_heading` fails with `'My report' != 'Real Title'`. The fallback and no-replacements tests pass already — they are regression cover for the restructuring in Step 4, which is the step that could break them.

- [ ] **Step 3: Implement the heading reader**

Add after `_derive_title` (line 490):

```python
MARKDOWN_HEADING = re.compile(r"^#[ \t]+(\S.*?)[ \t]*#*[ \t]*$", re.MULTILINE)


def markdown_title(source_path: Path) -> str | None:
    """The document's first level-one heading, or None when it has none.

    The stem rule sentence-cases, which lowercases initialisms:
    `AI_Education_Catalogue.md` becomes `Ai education catalogue`. The heading the
    author already wrote is the better starting point, and the result is still a
    proposal the user edits before publishing.
    """
    try:
        text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = MARKDOWN_HEADING.search(text)
    return match.group(1) if match else None
```

- [ ] **Step 4: Use it in the proposal**

In `propose_manifest_additions`, the entry loop currently sets `title=_derive_title(source.stem)` inside the `Entry(...)` construction. Replace the `if source.suffix.lower() == ".html":` / `else:` block and the `Entry(` construction so the title is computed first:

```python
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
```

This also removes the duplicated `source_root / source.as_posix()` expression that the `.html` branch built inline.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -B -m unittest tests.test_artefacts.ManifestProposalTests -v`
Expected: PASS, including the pre-existing `test_vendored_cdnjs_references_are_prefilled` and `test_titles_drop_ordering_prefixes_and_separators`.

- [ ] **Step 6: Run the full suite**

Run: `python3 -B -m unittest tests/test_artefacts.py`
Expected: OK.

- [ ] **Step 7: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py
git commit -m "feat: propose markdown titles from the first heading

The stem rule sentence-cases, so AI_Education_Catalogue.md proposed
'Ai education catalogue'. A markdown source now uses its first H1
verbatim and falls back to the stem rule when it has none."
```

---

### Task 8: End-to-end cycle and publish copy

Prove the whole path with an integration test, and fix the one piece of user-facing copy that still says Markdown is not published.

**Files:**
- Modify: `scripts/artefacts.py:1544-1546` (the PR body privacy paragraph)
- Test: `tests/test_artefacts.py` (`ApplyTests`)

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: nothing new.

- [ ] **Step 1: Write the failing integration test**

Add to `tests/test_artefacts.py`, at the end of `class ApplyTests`:

```python
    def test_markdown_add_then_update_cycle_validates(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        artefacts = repo / "artefacts"
        (artefacts / "vendor" / "marked.min.js").write_bytes(b"/* parser */")
        (artefacts / "notes.txt").unlink()  # keep validate's expected set exact
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["protected_files"].append("vendor/marked.min.js")
        payload["entries"] = [
            entry for entry in payload["entries"] if entry["id"] != "removed"
        ]
        payload["entries"].append(
            {
                "id": "notes-report",
                "source": "Notes/Report.md",
                "destination": "charts/report/index.html",
                "title": "Report",
                "collection": "charts",
                "order": 40,
                "replacements": {},
            }
        )
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        # The `removed` entry is gone from the manifest, so its published file would
        # be swept as an orphan; delete it here to keep the plan focused on Markdown.
        # Do NOT create Charts/Removed.png — an approved source with no entry raises
        # UnlistedSourceError and aborts the plan.
        (artefacts / "charts" / "removed.png").unlink()
        (source / "Notes").mkdir()
        report = source / "Notes" / "Report.md"
        report.write_text("# Report\n\nFirst version.\n", encoding="utf-8")

        head = manifest_path.read_bytes()
        first = artefacts_cli.create_sync_plan(manifest_path, source, artefacts, head)
        self.assertIn(
            artefacts_cli.Change("add", PurePosixPath("charts/report/index.html")),
            first.changes,
        )
        self.assertEqual(first.markdown_diffs, ())
        artefacts_cli.apply_plan(first, artefacts)
        artefacts_cli.validate_repository(repo, None)

        report.write_text("# Report\n\nSecond version.\n", encoding="utf-8")
        second = artefacts_cli.create_sync_plan(manifest_path, source, artefacts, head)
        self.assertIn(
            artefacts_cli.Change("update", PurePosixPath("charts/report/index.html")),
            second.changes,
        )
        body = dict(second.markdown_diffs)[PurePosixPath("charts/report/index.html")]
        self.assertIn("-First version.", body)
        self.assertIn("+Second version.", body)
        artefacts_cli.apply_plan(second, artefacts)
        artefacts_cli.validate_repository(repo, None)

        page = (artefacts / "charts" / "report" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            artefacts_cli.extract_markdown(page), "# Report\n\nSecond version.\n"
        )
```

The `notes.txt` deletion and the `removed` entry cleanup keep the fixture's tree exactly what `validate_repository` expects; without them the assertion fails on an unrelated `unexpected published file`. If the fixture drifts, read `make_fixture` (line 897) and adjust rather than loosening the assertions.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -B -m unittest tests.test_artefacts.ApplyTests.test_markdown_add_then_update_cycle_validates -v`
Expected: FAIL. Before Tasks 3-6 land it fails early; run it last so the failure is meaningful.

- [ ] **Step 3: Make it pass**

No production change should be needed — Tasks 1-7 cover the path. If it fails, the failure is a real integration gap; fix it in the owning function and note which task's assumption was wrong. Do not weaken the test.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -B -m unittest tests.test_artefacts.ApplyTests.test_markdown_add_then_update_cycle_validates -v`
Expected: PASS.

- [ ] **Step 5: Correct the pull-request copy**

In `publish`, replace the privacy paragraph at lines 1544-1546:

```python
        "## Privacy boundary\n\n"
        "Only manifest-listed HTML, PNG, JPEG, JPG, and ICO files are published. "
        "Excluded document types and local metadata remain private.\n\n"
```

with:

```python
        "## Privacy boundary\n\n"
        "Only manifest-listed HTML, Markdown, PNG, JPEG, JPG, and ICO files are "
        "published. Excluded document types and local metadata remain private.\n\n"
```

- [ ] **Step 6: Run the full suite and validate the repository**

```bash
python3 -B -m unittest tests/test_artefacts.py
python3 scripts/artefacts.py validate
```

Expected: OK, then `Validated N manifest entries and M local links.`

- [ ] **Step 7: Commit**

```bash
git add scripts/artefacts.py tests/test_artefacts.py
git commit -m "test: cover the markdown add-then-update publish cycle

One integration test walks a markdown source from add through update:
both plans classify correctly, the second carries the unified diff,
apply is byte-verified, and validate passes on each produced tree.

Also corrects the pull-request privacy paragraph, which still claimed
markdown was never published."
```

---

### Task 9: Dry run against the real source directory

The first real `plan` proposes roughly 32 entries across several new collections. That is expected and is a manifest-editing session, not a code change — but it must be seen before anyone runs `publish`.

**Files:**
- Modify: `artefacts/manifest.json` (pruned proposal, hand-edited prose)
- Test: none. The suite and `validate` are the gates.

**Interfaces:**
- Consumes: everything from Tasks 1-8.
- Produces: a manifest holding the Markdown entries the user chose to publish.

- [ ] **Step 1: Run `plan` and read the proposal**

Run: `python3 scripts/artefacts.py plan`
Expected: exit code 3 and a `Proposed collections` / `Proposed entries` block covering every `.md` under `~/Downloads/Artefacts`. Nothing is written.

- [ ] **Step 2: Show the user the proposal and get their selection**

Report the proposed collections and entries, then ask which Markdown documents should be published. Most sources are working notes — `prompts/`, `*-raw-*.md`, `analysis.md`, `structured-content.md`, `source-*.md`. Do not guess. Wait for the answer.

- [ ] **Step 3: Write the manifest additions**

Run: `python3 scripts/artefacts.py apply`, answer `yes` at the `Write these manifest additions?` prompt.
Expected: exit code 3, `Wrote artefacts/manifest.json`, and `artefacts/` untouched.

- [ ] **Step 4: Prune and edit the manifest**

Delete every proposed entry the user did not select, along with any collection left with no entries. Replace each `TODO: describe this collection.` with real prose. Fix any title the H1 got wrong.

- [ ] **Step 5: Re-run `plan` and read the preview**

Run: `python3 scripts/artefacts.py plan`
Expected: exit code 0, an `Add` list holding exactly the selected documents plus the updated `index.html` and `manifest.json`, and no `Delete (orphaned)` entries. If any `.md` is still unlisted, exit code 3 returns — repeat from Step 3.

- [ ] **Step 6: Apply and check a page in the browser**

```bash
python3 scripts/artefacts.py apply   # answer yes
python3 scripts/artefacts.py validate
python3 -m http.server 8765 --directory . >/dev/null 2>&1 &
echo "open http://localhost:8765/artefacts/"
```

Expected: `validate` passes. In the browser, the catalogue lists each new document, every link opens a rendered page, the theme toggle works, and the console is clean. Stop the server with `kill %1` when done.

- [ ] **Step 7: Commit the manifest and the generated tree**

```bash
git add artefacts
git commit -m "chore: publish markdown artefacts

Adds the selected Markdown documents to the manifest and the
generated pages to the published tree."
```

- [ ] **Step 8: Hand back to the user**

Report the published document list and the catalogue URL, and tell the user that `python3 scripts/artefacts.py publish` is the command that opens the pull request and deploys. Do not run `publish` without their explicit go-ahead: it pushes, merges, and deploys to the public site.

---

## Coverage against the design

| Design requirement | Task |
| --- | --- |
| `.md` in the approved scan set | 2 |
| `.md` follows the `.html` destination rule | 2 |
| `marked.min.js` vendored and protected | 1 |
| Markdown collections join the presentation section | inherited — `_sections_by_media` classifies on the destination suffix, which is `.html`; no code change |
| Self-contained shell matching the catalogue | 4 |
| `<title>` is the escaped manifest title | 4 |
| `</script` and `<!--` escaped, reversed in the page | 3, 4 |
| `extract_markdown` is the exact inverse | 3 |
| `markdown_diffs` on `SyncPlan` | 6 |
| Diff computed against the published page, no git | 6 |
| `Markdown changes (N)` in the preview and the PR body | 6 (the PR body embeds `format_plan`) |
| 40-line cap with the remaining count stated | 6 |
| Additions produce no diff | 6, 8 |
| Unextractable page reports `diff unavailable` | 3, 6 |
| Markdown entries declare no replacements | 5 |
| Proposal title from the first H1 | 7 |
| Markdown content preserved byte-exact | 3, 4 (no trailing-space stripping on the block) |
