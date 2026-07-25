# Publish Static Artefacts Under GitHub Pages

## Goal

Publish the image and HTML files from the local `Artefacts` directory at stable URLs under `https://kevinlin.github.io/artefacts/`, with no additional hosting cost and no change to the existing homepage.

## Scope

- Publish local `.html`, `.png`, `.jpeg`, `.jpg`, and `.ico` files.
- Do not publish Markdown, Word, or PDF documents.
- Keep each artefact in a descriptive subdirectory.
- Normalize public paths to lowercase kebab-case.
- Add a catalogue at `/artefacts/` that links to every published item.
- Add `.nojekyll` at the publishing root so GitHub Pages serves the files as static assets.

## Options Considered

1. Add an `/artefacts/` directory to this user-site repository. This is the selected option because the existing `main` branch already publishes to `kevinlin.github.io` and produces the requested subpath.
2. Create a separate GitHub Pages project repository. This adds another repository and deployment configuration without improving the public URL.
3. Use another free static host. This adds another account and deployment system without a current requirement that GitHub Pages cannot meet.

## Structure

Each HTML presentation gets a directory with an `index.html`, producing a clean directory URL. Images that belong to a presentation stay beside it or in an `images` directory. Standalone image collections retain their topic directory and use normalized filenames.

```text
artefacts/
  index.html
  snapdragon-product-line/
    index.html
  llm-effort-level-vs-performance/
    ...
  claude-code/
    ...
```

The catalogue is self-contained and uses the existing site's restrained blue, neutral, and responsive design language. It will not modify shared homepage CSS or JavaScript.

## External Dependencies

The two Chart.js pages currently load scripts from cdnjs. The implementation will store the required minified scripts under `artefacts/vendor/` and update those HTML files to use relative URLs. This keeps the published artefacts usable without a third-party runtime dependency.

## Validation

Before publishing:

- Confirm the source-to-public-path inventory contains only the approved file types.
- Check every local catalogue link and HTML asset reference resolves.
- Serve the site locally and request each published HTML page and image.
- Confirm the existing root page remains unchanged.

After merging:

- Wait for the GitHub Pages deployment to complete.
- Request the catalogue and every published artefact URL over HTTPS.
- Report the final catalogue URL and any exceptions.

## Security and Privacy

All published files are public and downloadable. Only the approved image and HTML file types are copied. Local metadata files and non-web documents remain outside the repository.
