# Kevin Lin - Professional Profile

This repository contains the code for my personal professional profile website, hosted on GitHub Pages.

## Important: Profile Photo

To use your LinkedIn profile photo:
1. Download your profile photo from LinkedIn
2. Rename it to `profile-photo.jpg`
3. Add it to the root directory of this repository

## Overview

This is a simple, responsive personal website that showcases my:
- Professional background
- Technical skills
- Featured projects
- Contact information

## Technologies Used

- HTML5
- CSS3
- Font Awesome for icons

## Development

To make changes to this website:

1. Clone this repository
2. Make your changes to the HTML/CSS files
3. Commit and push your changes to GitHub
4. GitHub Pages will automatically deploy the updated website

## Artefact publishing

Run the commands from an up-to-date `main` branch. The working tree must be clean, except for an optional unstaged edit to `artefacts/manifest.json`. GitHub CLI must be authenticated.

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

The command creates a branch and pull request, waits for all checks, merges with squash, waits for GitHub Pages, and verifies every public URL. It stops if local validation, GitHub checks, GitHub Pages, or public URL verification fails.

Only files listed in the manifest are published. Markdown, Word, PDF, metadata, and unlisted files remain private. Public destination paths are stable URLs. Do not change a destination only because its source filename changes.

## Visit the Website

The website is live at [https://kevinlin.github.io](https://kevinlin.github.io)

## License

This project is licensed under the MIT License.
