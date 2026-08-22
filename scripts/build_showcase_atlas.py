#!/usr/bin/env python3
"""Pack every published artefact image into one texture atlas for the 3D showcase.

The 34 published images total ~90 MB. A WebGL gallery cannot upload that, so the
showcase reads one atlas instead: a single request, a single GPU texture, and one
UV rect per panel. Clicking a panel still opens the full-resolution original.

Each image is contain-fitted into a square cell and never cropped, so a tall
infographic keeps its whole frame. The true aspect ratio travels with the UV rect
so the showcase can build each panel at its real shape.

Reads artefacts/manifest.json for what is public, in the same section/collection/
entry order the catalogue renders, so panel order matches the index page.

Writes artefacts/showcase/atlas.jpg and artefacts/showcase/atlas.js. Both are
generated and listed in the manifest's protected_files; re-run this after adding
or removing a published image.

Needs ffmpeg and ffprobe on PATH. Everything else is stdlib, per the repo's
no-pip-dependencies rule.

    python3 scripts/build_showcase_atlas.py
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

CELL = 512
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")
REPO_ROOT = Path(__file__).resolve().parent.parent
ARTEFACTS_ROOT = REPO_ROOT / "artefacts"


class AtlasError(Exception):
    pass


def published_images(manifest: dict) -> list[dict]:
    """Every image entry, ordered exactly as the catalogue renders it."""
    collections = {item["id"]: item for item in manifest["collections"]}
    items = []
    for entry in manifest["entries"]:
        destination = entry["destination"]
        if not destination.lower().endswith(IMAGE_SUFFIXES):
            continue
        collection = collections[entry["collection"]]
        items.append(
            {
                "id": entry["id"],
                "href": destination,
                "title": entry["title"],
                "collection": collection["title"],
                "_sort": (
                    collection["section_order"],
                    collection["order"],
                    entry["order"],
                ),
            }
        )
    items.sort(key=lambda item: item.pop("_sort"))
    return items


def probe_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def build(items: list[dict]) -> tuple[list[str], list[str], int, int]:
    """Measure every image and lay out its cell. Returns ffmpeg inputs and filters."""
    columns = math.ceil(math.sqrt(len(items)))
    rows = math.ceil(len(items) / columns)
    sheet_w, sheet_h = columns * CELL, rows * CELL

    inputs: list[str] = []
    filters: list[str] = []
    for index, item in enumerate(items):
        source = ARTEFACTS_ROOT / item["href"]
        if not source.is_file():
            raise AtlasError(f"published image is missing: {item['href']}")
        width, height = probe_size(source)
        aspect = width / height
        item["aspect"] = round(aspect, 6)

        inputs += ["-i", str(source)]
        filters.append(
            f"[{index}:v]scale={CELL}:{CELL}:force_original_aspect_ratio=decrease,"
            f"pad={CELL}:{CELL}:(ow-iw)/2:(oh-ih)/2:color=0x101010[c{index}]"
        )

        fit_w = CELL if aspect >= 1 else round(CELL * aspect)
        fit_h = CELL if aspect < 1 else round(CELL / aspect)
        origin_x = (index % columns) * CELL + (CELL - fit_w) / 2
        origin_y = (index // columns) * CELL + (CELL - fit_h) / 2
        # Half-texel inset: JPEG ringing at the pad boundary must never be sampled.
        item["uv"] = [
            round((origin_x + 0.5) / sheet_w, 6),
            round((origin_y + 0.5) / sheet_h, 6),
            round((fit_w - 1) / sheet_w, 6),
            round((fit_h - 1) / sheet_h, 6),
        ]

    layout = "|".join(
        f"{(i % columns) * CELL}_{(i // columns) * CELL}" for i in range(len(items))
    )
    filters.append(
        "".join(f"[c{i}]" for i in range(len(items)))
        + f"xstack=inputs={len(items)}:fill=0x101010:layout={layout}[out]"
    )
    return inputs, filters, columns, rows


def main() -> int:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise AtlasError(f"{tool} is required and was not found on PATH")

    manifest = json.loads((ARTEFACTS_ROOT / "manifest.json").read_text("utf-8"))
    items = published_images(manifest)
    if not items:
        raise AtlasError("no published images found in the manifest")

    inputs, filters, columns, rows = build(items)
    out_dir = ARTEFACTS_ROOT / "showcase"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_image = out_dir / "atlas.jpg"

    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
         "-map", "[out]", "-q:v", "4", str(out_image)],
        check=True, capture_output=True,
    )

    payload = json.dumps(
        {"columns": columns, "rows": rows, "cell": CELL, "panels": items},
        indent=1, ensure_ascii=False,
    )
    (out_dir / "atlas.js").write_text(
        "// Generated by scripts/build_showcase_atlas.py. Do not edit by hand.\n"
        f"export default {payload};\n",
        encoding="utf-8",
    )

    print(
        f"{len(items)} panels packed {columns}x{rows} at {CELL}px "
        f"-> artefacts/showcase/atlas.jpg ({out_image.stat().st_size / 1024:.0f} KB)"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AtlasError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
