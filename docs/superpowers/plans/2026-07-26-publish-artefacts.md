# Publish Static Artefacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the approved local image and HTML artefacts below `https://kevinlin.github.io/artefacts/` without changing the existing homepage.

**Architecture:** Add a self-contained `artefacts/` static tree to the existing GitHub Pages source. HTML presentations use directory entry points, standalone images use normalized filenames, and the catalogue owns its styles. Chart.js dependencies are stored under `artefacts/vendor/` and referenced with relative paths.

**Tech Stack:** Static HTML and CSS, GitHub Pages, Chart.js 4.4.1, chartjs-plugin-datalabels 2.2.0, Python HTTP server, Linkinator.

## Global Constraints

- Publish local `.html`, `.png`, `.jpeg`, `.jpg`, and `.ico` files only.
- Do not publish Markdown, Word, or PDF documents from the source directory.
- Normalize public paths to lowercase kebab-case.
- Do not modify root `index.html`, `styles.css`, or `script.js`.
- Serve Chart.js dependencies locally rather than from cdnjs.
- Treat every published file as public and downloadable.

---

## File Map

- `.nojekyll`
- `artefacts/index.html`
- `artefacts/vendor/chart.umd.min.js`
- `artefacts/vendor/chartjs-plugin-datalabels.min.js`
- `artefacts/snapdragon-product-line/index.html`
- `artefacts/llm-effort-level-vs-performance/intelligence-index/index.html`
- `artefacts/llm-effort-level-vs-performance/swe-bench-pro-by-lab/index.html`
- `artefacts/llm-effort-level-vs-performance/intelligence-index-chart.png`
- `artefacts/llm-effort-level-vs-performance/gpt-5-6-average-cost-per-task.png`
- `artefacts/llm-effort-level-vs-performance/gpt-5-6-average-output-tokens-per-task.png`
- `artefacts/llm-effort-level-vs-performance/sonnet-5-effort-dial-truth.png`
- `artefacts/gpt-5-6/first-five-days.png`
- `artefacts/claude-code/the-claude-code-leak.jpeg`
- `artefacts/claude-code/how-claude-code-leaked.jpeg`
- `artefacts/claude-code/shortcuts-cheatsheet.jpeg`
- `artefacts/peter-steinberger-human-taste/human-taste-1.png`
- `artefacts/peter-steinberger-human-taste/human-taste-2.png`
- `artefacts/people-lead-lifecycle/activities.png`
- `artefacts/ai-trend-slop/when-ai-gives-you-buzzwords.png`
- `artefacts/flow-fabric/icons/apple-touch-icon.png`
- `artefacts/flow-fabric/icons/favicon-16.png`
- `artefacts/flow-fabric/icons/favicon-32.png`
- `artefacts/flow-fabric/icons/favicon-64.png`
- `artefacts/flow-fabric/icons/favicon.ico`
- `artefacts/flow-fabric/icons/flow-fabric-icon-192.png`
- `artefacts/flow-fabric/icons/flow-fabric-icon-256.png`
- `artefacts/flow-fabric/icons/flow-fabric-icon-512.png`
- `artefacts/flow-fabric/icons/flow-fabric-icon-1024.png`

### Task 1: Build the Static Artefact Tree

**Files:**
- Create: every path in the File Map except `artefacts/index.html`
- Source: `/Users/kevinlin/Downloads/Artefacts/<topic>/<source-file>`

**Interfaces:**
- Consumes: the approved local HTML and image inventory.
- Produces: static pages whose runtime scripts resolve through repository-relative paths.

- [ ] **Step 1: Record and check the source inventory**

Run `find` for the five approved extensions while pruning `kevinlin.github.io`. Expected: three HTML files and 21 images/icons, with no Markdown, Word, or PDF files.

- [ ] **Step 2: Create the mapped directories and copy files**

Copy each image to its exact File Map path without transcoding. Copy each HTML source to its mapped `index.html`. Create the empty `.nojekyll` file.

- [ ] **Step 3: Vendor exact browser dependencies**

```bash
curl --fail --location https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js --output artefacts/vendor/chart.umd.min.js
curl --fail --location https://cdnjs.cloudflare.com/ajax/libs/chartjs-plugin-datalabels/2.2.0/chartjs-plugin-datalabels.min.js --output artefacts/vendor/chartjs-plugin-datalabels.min.js
test -s artefacts/vendor/chart.umd.min.js && test -s artefacts/vendor/chartjs-plugin-datalabels.min.js
```

Expected: both downloaded files are non-empty and the test exits 0.

- [ ] **Step 4: Replace cdnjs script references**

Use these relative references in the intelligence index page:

```html
<script src="../../vendor/chart.umd.min.js"></script>
<script src="../../vendor/chartjs-plugin-datalabels.min.js"></script>
```

Use this relative reference in the SWE-bench page:

```html
<script src="../../vendor/chart.umd.min.js"></script>
```

- [ ] **Step 5: Verify scope and byte-preserving image copies**

Compare every source and destination image with `shasum -a 256`. Run `find artefacts -type f` and compare it to the File Map. Run `rg -n 'cdnjs.cloudflare.com|https://cdnjs' artefacts --glob '*.html'` and require no output.

- [ ] **Step 6: Commit**

```bash
git add .nojekyll artefacts
git commit -m "feat: add static artefacts"
```

### Task 2: Add the Artefact Catalogue

**Files:**
- Create: `artefacts/index.html`

**Interfaces:**
- Consumes: stable relative paths from Task 1.
- Produces: `/artefacts/`, linking all three HTML presentations and every image collection.

- [ ] **Step 1: Write the catalogue**

Create one self-contained HTML5 document with:

- `<title>Artefacts | Kevin Lin</title>` and a `/` back link.
- Semantic `header`, `main`, grouped `section`, and `footer` elements.
- Cards for Snapdragon, LLM performance, GPT-5.6, Claude Code, Peter Steinberger, People Lead Lifecycle, AI trend slop, and Flow Fabric.
- Relative links for every artefact in the File Map.
- `#0063a3` structural links, `#ff5a5f` hover color, system sans-serif, neutral surfaces, 8px card radii.
- A `repeat(auto-fit, minmax(260px, 1fr))` grid, keyboard focus, and reduced-motion support.
- No shared stylesheet, JavaScript, analytics, build tooling, or external fonts.

- [ ] **Step 2: Check catalogue completeness**

Run `rg -o 'href="[^"]+"' artefacts/index.html`. Expected: links cover the three HTML entry points and all standalone image/icon paths.

- [ ] **Step 3: Validate local links**

```bash
python3 -m http.server 4173 --directory .
npx --yes linkinator http://127.0.0.1:4173/artefacts/ --recurse --skip 'https?://'
```

Expected: Linkinator reports zero broken local links.

- [ ] **Step 4: Confirm homepage isolation**

```bash
git diff origin/main -- index.html styles.css script.js
git diff --check
```

Expected: the homepage diff is empty and diff checking exits 0.

- [ ] **Step 5: Commit**

```bash
git add artefacts/index.html
git commit -m "feat: add artefact catalogue"
```

### Task 3: Publish and Verify GitHub Pages

**Files:**
- Verify: all files from Tasks 1 and 2
- Create: no additional site files

**Interfaces:**
- Consumes: the complete verified branch.
- Produces: public HTTPS URLs below `https://kevinlin.github.io/artefacts/`.

- [ ] **Step 1: Run complete local verification**

Require a clean branch, `git diff --check origin/main...HEAD` exit 0, zero Linkinator failures against `/artefacts/`, and HTTP 200 from both `/` and `/artefacts/` on the local server.

- [ ] **Step 2: Publish the branch and pull request**

Push `agent/publish-artefacts`. Open a pull request to `main` documenting the mapping, privacy boundary, and verification results.

- [ ] **Step 3: Merge the verified pull request**

Confirm required checks pass and merge using an accepted repository method. Do not merge if GitHub reports a failing required check or conflict.

- [ ] **Step 4: Wait for GitHub Pages**

Poll Pages status and the latest deployment until the merged commit is deployed or a concrete failure is reported.

- [ ] **Step 5: Verify public URLs**

Request `https://kevinlin.github.io/artefacts/` and every relative catalogue link. Require HTTP 200 for each. Also require HTTP 200 from `https://kevinlin.github.io/`.

- [ ] **Step 6: Report**

Return the merged pull request, deployed commit, catalogue URL, verified public-link count, and intentionally excluded file types.
