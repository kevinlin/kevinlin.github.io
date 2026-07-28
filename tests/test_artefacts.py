import contextlib
import importlib.util
import hashlib
from datetime import datetime
import io
import json
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "artefacts_cli", ROOT / "scripts" / "artefacts.py"
)
artefacts_cli = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = artefacts_cli
SPEC.loader.exec_module(artefacts_cli)


def valid_payload() -> dict:
    return {
        "version": 1,
        "protected_files": ["vendor/chart.umd.min.js"],
        "collections": [
            {
                "id": "charts",
                "title": "Charts",
                "description": "Data charts.",
                "section": "Analysis",
                "section_order": 10,
                "order": 10,
            }
        ],
        "entries": [
            {
                "id": "cost",
                "source": "Charts/Cost.png",
                "destination": "charts/cost.png",
                "title": "Cost",
                "collection": "charts",
                "order": 10,
                "replacements": {},
            }
        ],
    }


def second_entry() -> dict:
    return {
        "id": "tokens",
        "source": "Charts/Tokens.png",
        "destination": "charts/tokens.png",
        "title": "Tokens",
        "collection": "charts",
        "order": 20,
        "replacements": {},
    }


class ManifestTests(unittest.TestCase):
    def write_manifest(self, payload: dict) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def assert_manifest_error(self, payload: dict, message: str) -> None:
        with self.assertRaisesRegex(artefacts_cli.ManifestError, message):
            artefacts_cli.load_manifest(self.write_manifest(payload))

    def test_loads_valid_manifest(self):
        manifest = artefacts_cli.load_manifest(self.write_manifest(valid_payload()))

        self.assertEqual(manifest.version, 1)
        self.assertEqual(manifest.entries[0].destination.as_posix(), "charts/cost.png")
        self.assertEqual(
            manifest.protected_files[0].as_posix(), "vendor/chart.umd.min.js"
        )

    def test_rejects_unknown_manifest_version(self):
        payload = valid_payload()
        payload["version"] = 2

        self.assert_manifest_error(payload, "version must be 1")

    def test_rejects_duplicate_entry_ids(self):
        payload = valid_payload()
        duplicate = second_entry()
        duplicate["id"] = "cost"
        payload["entries"].append(duplicate)

        self.assert_manifest_error(payload, "duplicate entry id")

    def test_rejects_duplicate_destinations(self):
        payload = valid_payload()
        duplicate = second_entry()
        duplicate["destination"] = "charts/cost.png"
        payload["entries"].append(duplicate)

        self.assert_manifest_error(payload, "duplicate destination")

    def test_rejects_duplicate_sources(self):
        payload = valid_payload()
        duplicate = second_entry()
        duplicate["source"] = "Charts/Cost.png"
        payload["entries"].append(duplicate)

        self.assert_manifest_error(payload, "duplicate source")

    def test_rejects_protected_managed_path_overlap(self):
        payload = valid_payload()
        payload["protected_files"] = ["charts/cost.png"]

        self.assert_manifest_error(payload, "protected.*managed")

    def test_rejects_reserved_destination(self):
        payload = valid_payload()
        payload["entries"][0].update(
            {"source": "Charts/Index.html", "destination": "index.html"}
        )

        self.assert_manifest_error(payload, "reserved")

    def test_rejects_unknown_collection(self):
        payload = valid_payload()
        payload["entries"][0]["collection"] = "missing"

        self.assert_manifest_error(payload, "unknown collection")

    def test_rejects_parent_traversal(self):
        payload = valid_payload()
        payload["entries"][0]["destination"] = "../cost.png"

        self.assert_manifest_error(payload, "safe relative path")

    def test_rejects_uppercase_public_path(self):
        payload = valid_payload()
        payload["entries"][0]["destination"] = "Charts/Cost.png"

        self.assert_manifest_error(payload, "lowercase kebab-case")

    def test_rejects_unsupported_destination_extension(self):
        payload = valid_payload()
        payload["entries"][0]["source"] = "Charts/Cost.pdf"
        payload["entries"][0]["destination"] = "charts/cost.pdf"

        self.assert_manifest_error(payload, "unsupported source extension")

    def test_rejects_html_destination_without_directory_index(self):
        payload = valid_payload()
        payload["entries"][0]["source"] = "Charts/Cost.html"
        payload["entries"][0]["destination"] = "charts/cost.html"

        self.assert_manifest_error(payload, "must end in index.html")

    def test_rejects_image_extension_change(self):
        payload = valid_payload()
        payload["entries"][0]["destination"] = "charts/cost.jpg"

        self.assert_manifest_error(payload, "must keep source extension")

    def test_accepts_duplicate_orders(self):
        payload = valid_payload()
        duplicate = second_entry()
        duplicate["order"] = 10
        payload["entries"].append(duplicate)

        manifest = artefacts_cli.load_manifest(self.write_manifest(payload))

        self.assertEqual([entry.order for entry in manifest.entries], [10, 10])


class NormalizeOrdersTests(unittest.TestCase):
    def manifest_from(self, payload: dict):
        manifest = artefacts_cli.manifest_from_dict(payload)
        artefacts_cli.validate_manifest(manifest)
        return manifest

    def test_renumbers_duplicate_entry_orders_beside_their_twin(self):
        payload = valid_payload()
        payload["entries"][0]["order"] = 40
        first = second_entry()
        first["order"] = 10
        second = second_entry()
        second["id"] = "latency"
        second["source"] = "Charts/Latency.png"
        second["destination"] = "charts/latency.png"
        second["order"] = 10
        payload["entries"].extend([first, second])

        normalized = artefacts_cli.normalize_orders(self.manifest_from(payload))

        self.assertEqual(
            [(entry.id, entry.order) for entry in normalized.entries],
            [("cost", 30), ("tokens", 10), ("latency", 20)],
        )

    def test_leaves_unambiguous_orders_and_their_gaps_alone(self):
        payload = valid_payload()
        payload["entries"][0]["order"] = 10
        gapped = second_entry()
        gapped["order"] = 50
        payload["entries"].append(gapped)

        normalized = artefacts_cli.normalize_orders(self.manifest_from(payload))

        self.assertEqual([entry.order for entry in normalized.entries], [10, 50])

    def test_renumbers_only_the_collection_that_collides(self):
        payload = valid_payload()
        payload["collections"].append(
            {
                "id": "images",
                "title": "Images",
                "description": "Image files.",
                "section": "Analysis",
                "section_order": 10,
                "order": 40,
            }
        )
        other = second_entry()
        other["collection"] = "images"
        other["order"] = 70
        colliding = second_entry()
        colliding["id"] = "latency"
        colliding["source"] = "Charts/Latency.png"
        colliding["destination"] = "charts/latency.png"
        colliding["order"] = 10
        payload["entries"].extend([other, colliding])

        normalized = artefacts_cli.normalize_orders(self.manifest_from(payload))

        self.assertEqual(
            [(entry.id, entry.order) for entry in normalized.entries],
            [("cost", 10), ("tokens", 70), ("latency", 20)],
        )

    def test_renumbers_duplicate_collection_orders_within_a_section(self):
        payload = valid_payload()
        payload["collections"].append(
            {
                "id": "images",
                "title": "Images",
                "description": "Image files.",
                "section": "Analysis",
                "section_order": 10,
                "order": 10,
            }
        )

        normalized = artefacts_cli.normalize_orders(self.manifest_from(payload))

        self.assertEqual(
            [(collection.id, collection.order) for collection in normalized.collections],
            [("charts", 10), ("images", 20)],
        )


class SourceInventoryTests(unittest.TestCase):
    def make_source(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "topic").mkdir()
        return root

    def manifest_for(self, *sources: str):
        payload = valid_payload()
        payload["entries"] = []
        for order, source in enumerate(sources, start=1):
            suffix = PurePosixPath(source).suffix.lower()
            destination = f"charts/item-{order}{suffix}"
            if suffix == ".html":
                destination = f"charts/item-{order}/index.html"
            payload["entries"].append(
                {
                    "id": f"item-{order}",
                    "source": source,
                    "destination": destination,
                    "title": f"Item {order}",
                    "collection": "charts",
                    "order": order * 10,
                    "replacements": {},
                }
            )
        return artefacts_cli.manifest_from_dict(payload)

    def test_scan_reports_only_approved_files(self):
        root = self.make_source()
        (root / "topic" / "Chart.PNG").write_bytes(b"png")
        (root / "topic" / "notes.md").write_text("private", encoding="utf-8")
        (root / ".DS_Store").write_bytes(b"metadata")

        inventory = artefacts_cli.scan_source(root)

        self.assertEqual(inventory.approved, (PurePosixPath("topic/Chart.PNG"),))
        self.assertEqual(inventory.excluded_suffixes, (".md",))

    def test_scan_prunes_nested_repository_copy(self):
        root = self.make_source()
        nested = root / "topic" / "kevinlin.github.io"
        nested.mkdir()
        (nested / "private.png").write_bytes(b"private")
        (root / "topic" / "public.png").write_bytes(b"public")

        inventory = artefacts_cli.scan_source(root)

        self.assertEqual(inventory.approved, (PurePosixPath("topic/public.png"),))

    def test_scan_rejects_symbolic_links(self):
        root = self.make_source()
        target = root / "topic" / "target.png"
        target.write_bytes(b"png")
        (root / "topic" / "linked.png").symlink_to(target)

        with self.assertRaisesRegex(artefacts_cli.InventoryError, "symbolic link"):
            artefacts_cli.scan_source(root)

    def test_reconcile_rejects_unlisted_approved_source_and_carries_it(self):
        root = self.make_source()
        (root / "topic" / "New Chart.PNG").write_bytes(b"png")
        inventory = artefacts_cli.scan_source(root)
        manifest = self.manifest_for()

        with self.assertRaises(artefacts_cli.UnlistedSourceError) as caught:
            artefacts_cli.reconcile_inventory(manifest, inventory)

        self.assertEqual(caught.exception.unlisted, (PurePosixPath("topic/New Chart.PNG"),))
        self.assertIs(caught.exception.manifest, manifest)

    def test_reconcile_turns_missing_source_into_deletion_candidate(self):
        root = self.make_source()
        inventory = artefacts_cli.scan_source(root)
        manifest = self.manifest_for("topic/Missing.png")

        result = artefacts_cli.reconcile_inventory(manifest, inventory)

        self.assertEqual(tuple(entry.id for entry in result.missing_entries), ("item-1",))
        self.assertEqual(result.next_manifest.entries, ())


class ManifestProposalTests(unittest.TestCase):
    def make_source(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def propose(self, manifest, *sources: str, source_root: Path | None = None):
        return artefacts_cli.propose_manifest_additions(
            manifest,
            tuple(PurePosixPath(source) for source in sources),
            source_root or self.make_source(),
        )

    def test_collection_is_matched_through_an_existing_entry_source_folder(self):
        payload = valid_payload()
        payload["collections"][0]["id"] = "renamed-charts"
        payload["entries"][0]["collection"] = "renamed-charts"

        proposal = self.propose(
            artefacts_cli.manifest_from_dict(payload), "Charts/Extra.png"
        )

        self.assertEqual(proposal.collections, ())
        self.assertEqual(proposal.entries[0].collection, "renamed-charts")

    def test_new_image_collection_joins_the_existing_image_section(self):
        proposal = self.propose(
            artefacts_cli.manifest_from_dict(valid_payload()), "Travel/Map.png"
        )

        collection = proposal.collections[0]
        self.assertEqual(collection.id, "travel")
        self.assertEqual(collection.title, "Travel")
        self.assertEqual(collection.section, "Analysis")
        self.assertEqual(collection.description, artefacts_cli.PLACEHOLDER_DESCRIPTION)
        self.assertEqual(collection.section_order, 10)
        self.assertEqual(collection.order, 20)

    def test_new_html_collection_joins_the_existing_presentation_section(self):
        payload = valid_payload()
        payload["collections"].append(
            {
                "id": "decks",
                "title": "Decks",
                "description": "Slides.",
                "section": "Renamed presentations",
                "section_order": 20,
                "order": 10,
            }
        )
        payload["entries"].append(
            {
                "id": "deck",
                "source": "Decks/Deck.html",
                "destination": "decks/deck/index.html",
                "title": "Deck",
                "collection": "decks",
                "order": 10,
                "replacements": {},
            }
        )
        source_root = self.make_source()
        (source_root / "Timeline").mkdir()
        (source_root / "Timeline" / "Story.html").write_text("<p>x</p>", encoding="utf-8")

        proposal = self.propose(
            artefacts_cli.manifest_from_dict(payload),
            "Timeline/Story.html",
            "Timeline/Cover.png",
            source_root=source_root,
        )

        collection = proposal.collections[0]
        self.assertEqual(collection.section, "Renamed presentations")
        self.assertEqual(collection.section_order, 20)
        self.assertEqual(collection.order, 20)

    def test_section_constants_are_used_when_the_manifest_has_no_example(self):
        payload = valid_payload()
        payload["collections"] = []
        payload["entries"] = []
        source_root = self.make_source()
        (source_root / "Timeline").mkdir()
        (source_root / "Timeline" / "Story.html").write_text("<p>x</p>", encoding="utf-8")

        proposal = self.propose(
            artefacts_cli.manifest_from_dict(payload),
            "Timeline/Story.html",
            source_root=source_root,
        )

        collection = proposal.collections[0]
        self.assertEqual(collection.section, artefacts_cli.PRESENTATION_SECTION)
        self.assertEqual(collection.section_order, 10)
        self.assertEqual(collection.order, 10)

    def test_entry_id_collision_is_suffixed(self):
        payload = valid_payload()
        payload["entries"][0]["id"] = "charts-extra"

        proposal = self.propose(
            artefacts_cli.manifest_from_dict(payload), "Charts/Extra.png"
        )

        self.assertEqual(proposal.entries[0].id, "charts-extra-2")

    def test_titles_drop_ordering_prefixes_and_separators(self):
        proposal = self.propose(
            artefacts_cli.manifest_from_dict(valid_payload()),
            "Charts/01-iceberg-bright_dark-line.png",
        )

        self.assertEqual(proposal.entries[0].title, "Iceberg bright dark line")

    def test_orders_continue_from_the_collection_maximum(self):
        payload = valid_payload()
        payload["entries"].append(second_entry())

        proposal = self.propose(
            artefacts_cli.manifest_from_dict(payload),
            "Charts/Beta.png",
            "Charts/Alpha.png",
        )

        self.assertEqual(
            [(entry.source.name, entry.order) for entry in proposal.entries],
            [("Alpha.png", 30), ("Beta.png", 40)],
        )

    def test_vendored_cdnjs_references_are_prefilled(self):
        source_root = self.make_source()
        (source_root / "Charts").mkdir()
        (source_root / "Charts" / "Plot.html").write_text(
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/'
            'chart.umd.min.js"></script>\n'
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/other/1.0/other.js"></script>\n',
            encoding="utf-8",
        )

        proposal = self.propose(
            artefacts_cli.manifest_from_dict(valid_payload()),
            "Charts/Plot.html",
            source_root=source_root,
        )

        self.assertEqual(
            proposal.entries[0].replacements,
            {
                "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js":
                    "../../vendor/chart.umd.min.js"
            },
        )
        self.assertIn("unmapped cdnjs reference", proposal.warnings["charts-plot"])

    def test_fully_vendored_html_is_not_warned_about(self):
        source_root = self.make_source()
        (source_root / "Charts").mkdir()
        (source_root / "Charts" / "Plot.html").write_text(
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/'
            'chart.umd.min.js"></script>\n',
            encoding="utf-8",
        )

        proposal = self.propose(
            artefacts_cli.manifest_from_dict(valid_payload()),
            "Charts/Plot.html",
            source_root=source_root,
        )

        self.assertEqual(proposal.warnings, {})

    def test_unminified_reference_maps_to_the_vendored_minified_build(self):
        source_root = self.make_source()
        (source_root / "Charts").mkdir()
        (source_root / "Charts" / "Plot.html").write_text(
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/'
            'chart.umd.js"></script>\n',
            encoding="utf-8",
        )

        proposal = self.propose(
            artefacts_cli.manifest_from_dict(valid_payload()),
            "Charts/Plot.html",
            source_root=source_root,
        )

        self.assertEqual(
            proposal.entries[0].replacements,
            {
                "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js":
                    "../../vendor/chart.umd.min.js"
            },
        )
        self.assertEqual(proposal.warnings, {})

    def test_html_without_vendored_references_gets_no_replacements(self):
        source_root = self.make_source()
        (source_root / "Charts").mkdir()
        (source_root / "Charts" / "Plain.html").write_text("<p>x</p>", encoding="utf-8")

        proposal = self.propose(
            artefacts_cli.manifest_from_dict(valid_payload()),
            "Charts/Plain.html",
            source_root=source_root,
        )

        self.assertEqual(proposal.entries[0].replacements, {})

    def test_merged_proposal_is_a_valid_manifest(self):
        manifest = artefacts_cli.manifest_from_dict(valid_payload())
        proposal = self.propose(manifest, "Travel/Map.png")

        merged = artefacts_cli.merge_manifest_proposal(manifest, proposal)

        self.assertEqual(
            [entry.destination.as_posix() for entry in merged.entries],
            ["charts/cost.png", "travel/map.png"],
        )


class DesiredTreeTests(unittest.TestCase):
    def make_source_and_manifest(
        self,
        source: str,
        destination: str,
        replacements: dict[str, str] | None = None,
    ):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source_path = root / source
        source_path.parent.mkdir(parents=True)
        payload = valid_payload()
        payload["entries"][0].update(
            {
                "source": source,
                "destination": destination,
                "replacements": replacements or {},
            }
        )
        return root, artefacts_cli.manifest_from_dict(payload), source_path

    def test_build_desired_files_preserves_binary_bytes(self):
        root, manifest, source_path = self.make_source_and_manifest(
            "Images/Card.png", "images/card.png"
        )
        payload = b"\x89PNG\r\n\x1a\ncontent"
        source_path.write_bytes(payload)

        desired = artefacts_cli.build_desired_files(manifest, root)

        copied = desired[PurePosixPath("images/card.png")]
        self.assertEqual(copied, payload)
        self.assertEqual(hashlib.sha256(copied).digest(), hashlib.sha256(payload).digest())

    def test_transform_html_applies_replacement_and_removes_trailing_spaces(self):
        root, manifest, source_path = self.make_source_and_manifest(
            "Charts/Chart.html",
            "charts/chart/index.html",
            {"https://cdn.example/chart.js": "../../vendor/chart.js"},
        )
        source_path.write_text(
            '<script src="https://cdn.example/chart.js"></script>   \n<p>Chart</p>\t\n',
            encoding="utf-8",
        )

        desired = artefacts_cli.build_desired_files(manifest, root)

        self.assertEqual(
            desired[PurePosixPath("charts/chart/index.html")],
            b'<script src="../../vendor/chart.js"></script>\n<p>Chart</p>\n',
        )

    def test_transform_html_rejects_missing_declared_replacement(self):
        root, manifest, source_path = self.make_source_and_manifest(
            "Charts/Chart.html",
            "charts/chart/index.html",
            {"missing.js": "../../vendor/chart.js"},
        )
        source_path.write_text("<p>Chart</p>", encoding="utf-8")

        with self.assertRaisesRegex(
            artefacts_cli.TransformationError, "expected replacement not found"
        ):
            artefacts_cli.build_desired_files(manifest, root)

    def test_transform_html_adds_final_newline(self):
        root, manifest, source_path = self.make_source_and_manifest(
            "Charts/Chart.html", "charts/chart/index.html"
        )
        source_path.write_text("<p>Chart</p>", encoding="utf-8")

        desired = artefacts_cli.build_desired_files(manifest, root)

        self.assertEqual(
            desired[PurePosixPath("charts/chart/index.html")], b"<p>Chart</p>\n"
        )

    def test_transform_html_rejects_remaining_cdnjs_reference(self):
        root, manifest, source_path = self.make_source_and_manifest(
            "Charts/Chart.html", "charts/chart/index.html"
        )
        source_path.write_text(
            '<script src="https://cdnjs.cloudflare.com/chart.js"></script>',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            artefacts_cli.TransformationError,
            "forbidden cdnjs reference remains in .*: https://cdnjs.cloudflare.com/chart.js",
        ):
            artefacts_cli.build_desired_files(manifest, root)

    def test_build_desired_files_omits_missing_sources(self):
        root, manifest, _ = self.make_source_and_manifest(
            "Images/Missing.png", "images/missing.png"
        )

        desired = artefacts_cli.build_desired_files(manifest, root)

        self.assertEqual(desired, {})


class CatalogueTests(unittest.TestCase):
    def catalogue_manifest(self):
        payload = {
            "version": 1,
            "protected_files": ["vendor/chart.umd.min.js"],
            "collections": [
                {
                    "id": "images",
                    "title": "Images & icons",
                    "description": "Image <references>.",
                    "section": "Collections",
                    "section_order": 20,
                    "order": 10,
                },
                {
                    "id": "charts",
                    "title": "Charts",
                    "description": "Data charts.",
                    "section": "Analysis",
                    "section_order": 10,
                    "order": 10,
                },
            ],
            "entries": [
                {
                    "id": "image",
                    "source": "Images/Card.png",
                    "destination": "images/card.png",
                    "title": "Card <image>",
                    "collection": "images",
                    "order": 10,
                    "replacements": {},
                },
                {
                    "id": "chart",
                    "source": "Charts/Chart.html",
                    "destination": "charts/chart/index.html",
                    "title": "Chart",
                    "collection": "charts",
                    "order": 10,
                    "replacements": {},
                },
            ],
        }
        return artefacts_cli.manifest_from_dict(payload)

    def test_render_catalogue_orders_sections_and_escapes_text(self):
        rendered = artefacts_cli.render_catalogue(self.catalogue_manifest())

        self.assertLess(rendered.index("Analysis"), rendered.index("Collections"))
        self.assertIn("Images &amp; icons", rendered)
        self.assertIn("Image &lt;references&gt;.", rendered)
        self.assertIn("Card &lt;image&gt;", rendered)

    def test_render_catalogue_links_html_directories_and_images_once(self):
        rendered = artefacts_cli.render_catalogue(self.catalogue_manifest())

        self.assertEqual(rendered.count('href="charts/chart/"'), 1)
        self.assertEqual(rendered.count('href="images/card.png"'), 1)
        self.assertNotIn("vendor/chart.umd.min.js", rendered)

    def test_render_catalogue_shows_the_newest_source_date_per_card(self):
        manifest = artefacts_cli.manifest_from_dict(
            {
                "version": 1,
                "protected_files": [],
                "collections": [
                    {
                        "id": "images",
                        "title": "Images",
                        "description": "Images.",
                        "section": "Collections",
                        "section_order": 10,
                        "order": 10,
                    }
                ],
                "entries": [
                    {
                        "id": "old",
                        "source": "Images/Old.png",
                        "destination": "images/old.png",
                        "title": "Old",
                        "collection": "images",
                        "order": 10,
                        "replacements": {},
                    },
                    {
                        "id": "new",
                        "source": "Images/New.png",
                        "destination": "images/new.png",
                        "title": "New",
                        "collection": "images",
                        "order": 20,
                        "replacements": {},
                    },
                ],
            }
        )

        rendered = artefacts_cli.render_catalogue(
            manifest, {"old": "2026-01-05", "new": "2026-03-11"}
        )

        self.assertEqual(rendered.count('class="card-updated"'), 1)
        self.assertIn('<time datetime="2026-03-11">2026-03-11</time>', rendered)
        self.assertNotIn("2026-01-05", rendered)

    def sorting_manifest(self):
        collections = [
            {
                "id": identifier,
                "title": identifier.title(),
                "description": "Cards.",
                "section": "Collections",
                "section_order": 10,
                "order": order,
            }
            for identifier, order in (("first", 10), ("second", 20), ("third", 30))
        ]
        entries = [
            {
                "id": identifier,
                "source": f"Images/{identifier}.png",
                "destination": f"images/{identifier}.png",
                "title": identifier.title(),
                "collection": identifier,
                "order": 10,
                "replacements": {},
            }
            for identifier, _ in (("first", 10), ("second", 20), ("third", 30))
        ]
        return artefacts_cli.manifest_from_dict(
            {
                "version": 1,
                "protected_files": [],
                "collections": collections,
                "entries": entries,
            }
        )

    def test_render_catalogue_sorts_cards_by_newest_date_first(self):
        rendered = artefacts_cli.render_catalogue(
            self.sorting_manifest(),
            {"first": "2026-01-05", "second": "2026-06-30", "third": "2026-03-11"},
        )

        self.assertLess(rendered.index("Second"), rendered.index("Third"))
        self.assertLess(rendered.index("Third"), rendered.index("First"))

    def test_render_catalogue_sorts_undated_cards_last_by_declared_order(self):
        rendered = artefacts_cli.render_catalogue(
            self.sorting_manifest(), {"third": "2026-03-11"}
        )

        self.assertLess(rendered.index("Third"), rendered.index("First"))
        self.assertLess(rendered.index("First"), rendered.index("Second"))

    def test_render_catalogue_omits_the_date_without_timestamps(self):
        rendered = artefacts_cli.render_catalogue(self.catalogue_manifest())

        self.assertNotIn("card-updated", rendered)

    def test_collect_source_timestamps_reads_mtime_and_skips_missing_sources(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        source = Path(directory.name)
        (source / "Images").mkdir()
        (source / "Images" / "Card.png").write_bytes(b"png")
        os.utime(source / "Images" / "Card.png", (1767225600, 1767225600))

        timestamps = artefacts_cli.collect_source_timestamps(
            self.catalogue_manifest(), source
        )

        self.assertEqual(
            timestamps,
            {
                "image": datetime.fromtimestamp(1767225600).strftime("%Y-%m-%d"),
            },
        )

    def test_replace_generated_catalogue_changes_only_marker_region(self):
        document = "before\n<!-- ARTEFACTS:START -->\nold\n<!-- ARTEFACTS:END -->\nafter\n"

        updated = artefacts_cli.replace_generated_catalogue(document, "new")

        self.assertEqual(
            updated,
            "before\n<!-- ARTEFACTS:START -->\nnew\n<!-- ARTEFACTS:END -->\nafter\n",
        )

    def test_replace_generated_catalogue_rejects_missing_markers(self):
        with self.assertRaisesRegex(artefacts_cli.CatalogueError, "exactly one"):
            artefacts_cli.replace_generated_catalogue("no markers", "new")

    def test_replace_generated_catalogue_preserves_end_marker_indentation(self):
        document = (
            "    <!-- ARTEFACTS:START -->\n"
            "    old\n"
            "    <!-- ARTEFACTS:END -->\n"
        )

        updated = artefacts_cli.replace_generated_catalogue(document, "    new")

        self.assertEqual(
            updated,
            "    <!-- ARTEFACTS:START -->\n"
            "    new\n"
            "    <!-- ARTEFACTS:END -->\n",
        )

    def test_replace_generated_catalogue_rejects_duplicate_markers(self):
        document = (
            "<!-- ARTEFACTS:START --><!-- ARTEFACTS:START -->"
            "<!-- ARTEFACTS:END -->"
        )

        with self.assertRaisesRegex(artefacts_cli.CatalogueError, "exactly one"):
            artefacts_cli.replace_generated_catalogue(document, "new")


class ArtefactFixture:
    """Temporary repository and source roots shared by the apply-side tests."""

    def make_fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        repo = root / "repo"
        source = root / "source"
        artefacts = repo / "artefacts"
        source.mkdir(parents=True)
        artefacts.mkdir(parents=True)

        payload = valid_payload()
        payload["protected_files"] = ["vendor/chart.js"]
        payload["entries"] = [
            {
                "id": "existing",
                "source": "Charts/Existing.png",
                "destination": "charts/existing.png",
                "title": "Existing",
                "collection": "charts",
                "order": 10,
                "replacements": {},
            },
            {
                "id": "new",
                "source": "Charts/New.png",
                "destination": "charts/new.png",
                "title": "New",
                "collection": "charts",
                "order": 20,
                "replacements": {},
            },
            {
                "id": "removed",
                "source": "Charts/Removed.png",
                "destination": "charts/removed.png",
                "title": "Removed",
                "collection": "charts",
                "order": 30,
                "replacements": {},
            },
        ]
        manifest_path = artefacts / "manifest.json"
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (artefacts / "index.html").write_text(
            "<main>\n<!-- ARTEFACTS:START -->\nold\n<!-- ARTEFACTS:END -->\n</main>\n",
            encoding="utf-8",
        )
        (artefacts / "vendor").mkdir()
        (artefacts / "vendor" / "chart.js").write_bytes(b"vendor")
        (artefacts / "notes.txt").write_text("keep", encoding="utf-8")
        (artefacts / "charts").mkdir()
        (artefacts / "charts" / "existing.png").write_bytes(b"old")
        (artefacts / "charts" / "removed.png").write_bytes(b"remove")
        (source / "Charts").mkdir()
        (source / "Charts" / "Existing.png").write_bytes(b"updated")
        (source / "Charts" / "New.png").write_bytes(b"new")
        head_manifest = manifest_path.read_bytes()
        return repo, source, manifest_path, head_manifest

    def snapshot(self, root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }


class ApplyTests(ArtefactFixture, unittest.TestCase):
    def test_create_sync_plan_classifies_generated_and_source_changes(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        changes = {(change.kind, change.destination.as_posix()) for change in plan.changes}
        self.assertEqual(
            changes,
            {
                ("update", "charts/existing.png"),
                ("add", "charts/new.png"),
                ("delete", "charts/removed.png"),
                ("orphan", "notes.txt"),
                ("update", "index.html"),
                ("update", "manifest.json"),
            },
        )

    def test_plan_renumbers_duplicate_orders_and_reports_them(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["entries"][1]["order"] = 10
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        self.assertEqual(
            [(entry.id, entry.order) for entry in plan.next_manifest.entries],
            [("existing", 10), ("new", 20)],
        )
        written = json.loads(plan.desired_files[PurePosixPath("manifest.json")])
        self.assertEqual([entry["order"] for entry in written["entries"]], [10, 20])
        self.assertIn("  ~ new: 10 -> 20", artefacts_cli.format_plan(plan))

    def test_plan_calculation_does_not_mutate_repository(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        before = self.snapshot(repo)

        artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        self.assertEqual(self.snapshot(repo), before)

    def test_destination_change_deletes_previous_public_path(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["entries"][0]["destination"] = "charts/renamed.png"
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        changes = {(change.kind, change.destination.as_posix()) for change in plan.changes}
        self.assertIn(("add", "charts/renamed.png"), changes)
        self.assertIn(("delete", "charts/existing.png"), changes)

        artefacts_cli.apply_plan(plan, manifest_path, repo / "artefacts")

        self.assertFalse((repo / "artefacts/charts/existing.png").exists())
        self.assertEqual((repo / "artefacts/charts/renamed.png").read_bytes(), b"updated")

    def test_removed_manifest_entry_deletes_previous_public_path(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["entries"] = [
            entry for entry in payload["entries"] if entry["id"] != "removed"
        ]
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        changes = {(change.kind, change.destination.as_posix()) for change in plan.changes}
        self.assertIn(("delete", "charts/removed.png"), changes)

    def test_reclassified_protected_path_is_not_deleted(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["protected_files"].append("charts/existing.png")
        payload["entries"][0]["destination"] = "charts/renamed.png"
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        changes = {(change.kind, change.destination.as_posix()) for change in plan.changes}
        self.assertNotIn(("delete", "charts/existing.png"), changes)

        artefacts_cli.apply_plan(plan, manifest_path, repo / "artefacts")

        self.assertEqual((repo / "artefacts/charts/existing.png").read_bytes(), b"old")

    def test_broken_desired_reference_fails_without_mutating_repository(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["entries"][0].update(
            {
                "source": "Charts/Existing.html",
                "destination": "charts/existing/index.html",
            }
        )
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (source / "Charts" / "Existing.png").unlink()
        (source / "Charts" / "Existing.html").write_text(
            '<script src="../../vendor/missing.js"></script>\n', encoding="utf-8"
        )
        before = self.snapshot(repo)

        with self.assertRaisesRegex(artefacts_cli.ValidationError, "broken local reference"):
            artefacts_cli.create_sync_plan(
                manifest_path, source, repo / "artefacts", head_manifest
            )

        self.assertEqual(self.snapshot(repo), before)

    def test_complete_add_update_delete_cycle(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        artefacts_cli.apply_plan(plan, manifest_path, repo / "artefacts")

        self.assertEqual((repo / "artefacts/charts/existing.png").read_bytes(), b"updated")
        self.assertEqual((repo / "artefacts/charts/new.png").read_bytes(), b"new")
        self.assertFalse((repo / "artefacts/charts/removed.png").exists())
        self.assertEqual((repo / "artefacts/vendor/chart.js").read_bytes(), b"vendor")
        self.assertFalse((repo / "artefacts/notes.txt").exists())
        applied_manifest = artefacts_cli.load_manifest(manifest_path)
        self.assertEqual([entry.id for entry in applied_manifest.entries], ["existing", "new"])
        catalogue = (repo / "artefacts/index.html").read_text()
        self.assertIn('href="charts/new.png"', catalogue)
        self.assertNotIn("Removed", catalogue)
        self.assertFalse(any(repo.rglob("*.tmp")))

    def test_orphan_sweep_spares_protected_files_and_ignored_metadata(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        (repo / "artefacts" / ".DS_Store").write_bytes(b"metadata")

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        orphans = {
            change.destination.as_posix()
            for change in plan.changes
            if change.kind == "orphan"
        }
        self.assertEqual(orphans, {"notes.txt"})

        artefacts_cli.apply_plan(plan, manifest_path, repo / "artefacts")

        self.assertEqual((repo / "artefacts/.DS_Store").read_bytes(), b"metadata")
        self.assertEqual((repo / "artefacts/vendor/chart.js").read_bytes(), b"vendor")

    def test_folder_renamed_without_a_manifest_change_resolves_in_one_apply(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        artefacts_root = repo / "artefacts"
        renamed = artefacts_root / "renamed"
        (artefacts_root / "charts").rename(renamed)

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, artefacts_root, head_manifest
        )

        changes = {(change.kind, change.destination.as_posix()) for change in plan.changes}
        self.assertIn(("add", "charts/existing.png"), changes)
        self.assertIn(("orphan", "renamed/existing.png"), changes)
        self.assertIn(("orphan", "renamed/removed.png"), changes)

        artefacts_cli.apply_plan(plan, manifest_path, artefacts_root)

        self.assertFalse(renamed.exists())
        self.assertEqual((artefacts_root / "charts/existing.png").read_bytes(), b"updated")

    def test_orphan_set_matches_repository_validation_rejection(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        artefacts_root = repo / "artefacts"
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, artefacts_root, head_manifest
        )
        published, _ = artefacts_cli.scan_published_tree(artefacts_root)
        manifest = artefacts_cli.load_manifest(manifest_path)
        expected = {
            PurePosixPath("index.html"),
            PurePosixPath("manifest.json"),
            *manifest.protected_files,
            *(entry.destination for entry in manifest.entries),
        }

        unexpected = artefacts_cli.unexpected_published_files(published, expected)

        self.assertEqual(
            [change.destination for change in plan.changes if change.kind == "orphan"],
            unexpected,
        )

    def test_apply_sweeps_orphan_without_touching_published_files(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        artefacts_root = repo / "artefacts"
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, artefacts_root, head_manifest
        )

        artefacts_cli.apply_plan(plan, manifest_path, artefacts_root)
        report = artefacts_cli.validate_repository(repo, None)

        self.assertEqual(report.entry_count, 2)
        applied = self.snapshot(artefacts_root)
        self.assertEqual(
            set(applied),
            {
                "manifest.json",
                "index.html",
                "vendor/chart.js",
                "charts/existing.png",
                "charts/new.png",
            },
        )

    def test_confirmation_other_than_yes_does_not_apply(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )
        before = self.snapshot(repo)

        applied = artefacts_cli.confirm_and_apply(
            plan,
            manifest_path,
            repo / "artefacts",
            lambda _: "no",
        )

        self.assertFalse(applied)
        self.assertEqual(self.snapshot(repo), before)

    def test_format_plan_lists_each_change_kind_and_excluded_types(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        (source / "notes.md").write_text("private", encoding="utf-8")
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        output = artefacts_cli.format_plan(plan)

        self.assertIn("Add (1)\n  + charts/new.png", output)
        self.assertIn("Update (3)", output)
        self.assertIn("Delete (1)\n  - charts/removed.png", output)
        self.assertIn("Delete (orphaned) (1)\n  - notes.txt", output)
        self.assertIn("Excluded source types: .md", output)


class UnlistedSourceCommandTests(ArtefactFixture, unittest.TestCase):
    def make_fixture_with_unlisted(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        (source / "Travel").mkdir()
        (source / "Travel" / "Map.png").write_bytes(b"map")
        return repo, source, manifest_path

    def run_command(self, command: str, repo: Path, source: Path, answer: str = "yes"):
        return artefacts_cli.main(
            [command, "--repo", str(repo), "--source", str(source)],
            input_fn=lambda _: answer,
        )

    def test_plan_reports_the_proposal_without_writing(self):
        repo, source, _ = self.make_fixture_with_unlisted()
        before = self.snapshot(repo)

        code = self.run_command("plan", repo, source)

        self.assertEqual(code, 3)
        self.assertEqual(self.snapshot(repo), before)

    def test_apply_writes_only_the_manifest_and_stops(self):
        repo, source, manifest_path = self.make_fixture_with_unlisted()
        before = self.snapshot(repo)

        code = self.run_command("apply", repo, source)

        self.assertEqual(code, 3)
        after = self.snapshot(repo)
        self.assertEqual(
            {path for path, content in after.items() if before.get(path) != content},
            {"artefacts/manifest.json"},
        )
        manifest = artefacts_cli.load_manifest(manifest_path)
        self.assertIn("travel", [collection.id for collection in manifest.collections])
        self.assertIn(
            PurePosixPath("travel/map.png"),
            [entry.destination for entry in manifest.entries],
        )

    def test_declining_the_proposal_writes_nothing(self):
        repo, source, _ = self.make_fixture_with_unlisted()
        before = self.snapshot(repo)

        code = self.run_command("apply", repo, source, answer="no")

        self.assertEqual(code, 2)
        self.assertEqual(self.snapshot(repo), before)

    def test_second_apply_run_publishes_the_proposed_entry(self):
        repo, source, _ = self.make_fixture_with_unlisted()
        self.run_command("apply", repo, source)

        code = self.run_command("apply", repo, source)

        self.assertEqual(code, 0)
        self.assertEqual((repo / "artefacts/travel/map.png").read_bytes(), b"map")
        self.assertIn("travel/map.png", (repo / "artefacts/index.html").read_text())


class RepositoryValidationTests(unittest.TestCase):
    def valid_repository(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        repo = Path(directory.name)
        artefacts = repo / "artefacts"
        (artefacts / "charts" / "chart").mkdir(parents=True)
        (artefacts / "images").mkdir()
        (artefacts / "vendor").mkdir()
        payload = {
            "version": 1,
            "protected_files": ["vendor/chart.umd.min.js"],
            "collections": [
                {
                    "id": "charts",
                    "title": "Charts",
                    "description": "Published charts.",
                    "section": "Analysis",
                    "section_order": 10,
                    "order": 10,
                }
            ],
            "entries": [
                {
                    "id": "chart",
                    "source": "Charts/Chart.html",
                    "destination": "charts/chart/index.html",
                    "title": "Chart",
                    "collection": "charts",
                    "order": 10,
                    "replacements": {},
                },
                {
                    "id": "image",
                    "source": "Charts/Image.png",
                    "destination": "images/image.png",
                    "title": "Image",
                    "collection": "charts",
                    "order": 20,
                    "replacements": {},
                },
            ],
        }
        manifest_path = artefacts / "manifest.json"
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        manifest = artefacts_cli.load_manifest(manifest_path)
        catalogue = (
            '<a href="/">Home</a>\n'
            "<!-- ARTEFACTS:START -->\n"
            "old\n"
            "<!-- ARTEFACTS:END -->\n"
        )
        (artefacts / "index.html").write_text(
            artefacts_cli.replace_generated_catalogue(
                catalogue, artefacts_cli.render_catalogue(manifest)
            ),
            encoding="utf-8",
        )
        (artefacts / "charts" / "chart" / "index.html").write_text(
            '<script src="../../vendor/chart.umd.min.js"></script>\n',
            encoding="utf-8",
        )
        (artefacts / "images" / "image.png").write_bytes(b"png")
        (artefacts / "vendor" / "chart.umd.min.js").write_bytes(b"chart")
        (repo / "index.html").write_text("home\n", encoding="utf-8")
        (repo / "styles.css").write_text("body {}\n", encoding="utf-8")
        (repo / "script.js").write_text("// home\n", encoding="utf-8")
        return repo

    def test_validate_accepts_complete_repository(self):
        report = artefacts_cli.validate_repository(self.valid_repository(), None)

        self.assertEqual(report.entry_count, 2)
        self.assertEqual(report.local_link_count, 4)

    def test_validate_rejects_unexpected_published_file(self):
        repo = self.valid_repository()
        (repo / "artefacts" / "unlisted.png").write_bytes(b"png")

        with self.assertRaisesRegex(
            artefacts_cli.ValidationError, "unexpected published file"
        ):
            artefacts_cli.validate_repository(repo, None)

    def test_validate_rejects_missing_destination(self):
        repo = self.valid_repository()
        (repo / "artefacts" / "images" / "image.png").unlink()

        with self.assertRaisesRegex(artefacts_cli.ValidationError, "missing published file"):
            artefacts_cli.validate_repository(repo, None)

    def test_validate_ignores_ds_store(self):
        repo = self.valid_repository()
        (repo / "artefacts" / ".DS_Store").write_bytes(b"metadata")

        report = artefacts_cli.validate_repository(repo, None)

        self.assertEqual(report.ignored_metadata_count, 1)

    def test_validate_rejects_missing_catalogue_link(self):
        repo = self.valid_repository()
        catalogue = repo / "artefacts" / "index.html"
        catalogue.write_text(
            catalogue.read_text().replace('href="images/image.png"', 'href="images/missing.png"'),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(artefacts_cli.ValidationError, "catalogue link"):
            artefacts_cli.validate_repository(repo, None)

    def test_validate_rejects_duplicate_catalogue_link(self):
        repo = self.valid_repository()
        catalogue = repo / "artefacts" / "index.html"
        catalogue.write_text(
            catalogue.read_text().replace(
                "<!-- ARTEFACTS:END -->",
                '<a href="images/image.png">Duplicate</a>\n<!-- ARTEFACTS:END -->',
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(artefacts_cli.ValidationError, "catalogue link"):
            artefacts_cli.validate_repository(repo, None)

    def test_validate_rejects_broken_relative_script(self):
        repo = self.valid_repository()
        page = repo / "artefacts" / "charts" / "chart" / "index.html"
        page.write_text('<script src="../../vendor/missing.js"></script>\n', encoding="utf-8")

        with self.assertRaisesRegex(artefacts_cli.ValidationError, "broken local reference"):
            artefacts_cli.validate_repository(repo, None)

    def test_validate_rejects_cdnjs_reference(self):
        repo = self.valid_repository()
        page = repo / "artefacts" / "charts" / "chart" / "index.html"
        page.write_text(
            '<script src="https://cdnjs.cloudflare.com/chart.js"></script>\n',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(artefacts_cli.ValidationError, "forbidden cdnjs"):
            artefacts_cli.validate_repository(repo, None)

    def test_validate_rejects_homepage_commit_diff(self):
        repo = self.valid_repository()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
        ).stdout.strip()
        (repo / "index.html").write_text("changed\n", encoding="utf-8")
        subprocess.run(["git", "add", "index.html"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "change home"], cwd=repo, check=True)

        with self.assertRaisesRegex(artefacts_cli.ValidationError, "homepage files changed"):
            artefacts_cli.validate_repository(repo, base)


class RecordingRunner:
    def __init__(
        self,
        head_manifest: bytes,
        *,
        status: str = "",
        branch: str = "main",
        divergence: str = "0\t0\n",
        checks: list[dict] | None = None,
        watch_returncode: int = 0,
        pages: list[dict] | None = None,
        bad_url: str | None = None,
        failures: dict[tuple[str, ...], str] | None = None,
        check_responses: list[list[dict]] | None = None,
    ):
        self.head_manifest = head_manifest.decode("utf-8")
        self.status = status
        self.branch = branch
        self.divergence = divergence
        self.checks = checks if checks is not None else [{"name": "validate", "bucket": "pass"}]
        self.check_responses = list(check_responses or [self.checks])
        self.watch_returncode = watch_returncode
        self.pages = list(pages or [{"status": "built", "commit": "merge123", "error": {"message": None}}])
        self.bad_url = bad_url
        self.failures = failures or {}
        self.commands: list[list[str]] = []
        self.pr_body = ""

    def __call__(self, args: list[str], cwd: Path):
        self.commands.append(list(args))
        stdout = ""
        stderr = ""
        returncode = 0
        for prefix, message in self.failures.items():
            if args[: len(prefix)] == list(prefix):
                return SimpleNamespace(stdout="", stderr=message, returncode=1)
        if args[:3] == ["git", "status", "--porcelain"]:
            stdout = self.status
        elif args[:2] == ["git", "branch"]:
            stdout = self.branch + "\n"
        elif args[:2] == ["git", "rev-list"]:
            stdout = self.divergence
        elif args[:3] == ["git", "show", "HEAD:artefacts/manifest.json"]:
            stdout = self.head_manifest
        elif args[:3] == ["gh", "pr", "create"]:
            body_path = Path(args[args.index("--body-file") + 1])
            self.pr_body = body_path.read_text(encoding="utf-8")
            stdout = "https://github.com/example/site/pull/7\n"
        elif args[:3] == ["gh", "pr", "checks"] and "--watch" in args:
            returncode = self.watch_returncode
        elif args[:3] == ["gh", "pr", "checks"] and "--json" in args:
            stdout = json.dumps(
                self.check_responses.pop(0)
                if len(self.check_responses) > 1
                else self.check_responses[0]
            )
        elif args[:3] == ["gh", "pr", "view"]:
            stdout = json.dumps(
                {
                    "state": "MERGED",
                    "mergeCommit": {"oid": "merge123"},
                    "url": "https://github.com/example/site/pull/7",
                }
            )
        elif args[:3] == ["gh", "repo", "view"]:
            stdout = json.dumps({"nameWithOwner": "example/site"})
        elif args[:3] == ["gh", "api", "repos/example/site/pages"]:
            stdout = json.dumps({"html_url": "https://kevinlin.github.io/"})
        elif args[:3] == ["gh", "api", "repos/example/site/pages/builds/latest"]:
            stdout = json.dumps(self.pages.pop(0) if len(self.pages) > 1 else self.pages[0])
        elif args and args[0] == "curl":
            target = args[-1]
            stdout = "404" if target == self.bad_url else "200"
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

    def called(self, prefix: list[str]) -> bool:
        return any(command[: len(prefix)] == prefix for command in self.commands)


class PublishingTests(unittest.TestCase):
    def make_repository(self, changed: bool = True):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        repo = Path(directory.name) / "repo"
        source = Path(directory.name) / "source"
        (repo / "artefacts" / "images").mkdir(parents=True)
        (source / "Images").mkdir(parents=True)
        payload = valid_payload()
        payload["protected_files"] = []
        payload["entries"][0].update(
            {
                "source": "Images/Card.png",
                "destination": "images/card.png",
                "title": "Card",
            }
        )
        manifest_path = repo / "artefacts" / "manifest.json"
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        manifest = artefacts_cli.load_manifest(manifest_path)
        catalogue_shell = (
            '<a href="/">Home</a>\n'
            "<!-- ARTEFACTS:START -->\n"
            "old\n"
            "<!-- ARTEFACTS:END -->\n"
        )
        current = b"old"
        (repo / "artefacts" / "images" / "card.png").write_bytes(current)
        (source / "Images" / "Card.png").write_bytes(b"new" if changed else current)
        (repo / "artefacts" / "index.html").write_text(
            artefacts_cli.replace_generated_catalogue(
                catalogue_shell,
                artefacts_cli.render_catalogue(
                    manifest, artefacts_cli.collect_source_timestamps(manifest, source)
                ),
            ),
            encoding="utf-8",
        )
        (repo / "index.html").write_text("home\n", encoding="utf-8")
        (repo / "styles.css").write_text("body {}\n", encoding="utf-8")
        (repo / "script.js").write_text("// home\n", encoding="utf-8")
        return repo, source, manifest_path.read_bytes()

    def fixed_now(self):
        return datetime(2026, 7, 26, 14, 30, 0)

    def test_parser_accepts_publish_with_repo_and_source(self):
        args = artefacts_cli._parser().parse_args(
            ["publish", "--repo", "/tmp/site", "--source", "/tmp/source"]
        )

        self.assertEqual(args.command, "publish")
        self.assertEqual(args.repo, Path("/tmp/site"))
        self.assertEqual(args.source, Path("/tmp/source"))

    def publish(self, repo: Path, source: Path, runner: RecordingRunner, confirm=lambda _: "yes"):
        return artefacts_cli.publish(
            repo,
            source,
            runner,
            confirm,
            self.fixed_now,
            lambda _: None,
        )

    def test_publish_allows_one_unstaged_manifest_edit(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(head, status=" M artefacts/manifest.json\n")

        result = self.publish(repo, source, runner)

        self.assertEqual(result.merge_commit, "merge123")

    def test_publish_rejects_missing_required_command(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(head, failures={("curl", "--version"): "not found"})

        with self.assertRaisesRegex(artefacts_cli.PublishError, "curl.*not found"):
            self.publish(repo, source, runner)

        self.assertFalse(runner.called(["git", "switch"]))

    def test_publish_rejects_unauthenticated_github_cli(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(
            head, failures={("gh", "auth", "status"): "not logged in"}
        )

        with self.assertRaisesRegex(artefacts_cli.PublishError, "not authenticated"):
            self.publish(repo, source, runner)

        self.assertFalse(runner.called(["git", "switch"]))

    def test_publish_rejects_staged_changes(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(head, status="M  artefacts/manifest.json\n")

        with self.assertRaisesRegex(artefacts_cli.PublishError, "working tree"):
            self.publish(repo, source, runner)

        self.assertFalse(runner.called(["git", "switch"]))

    def test_publish_rejects_unrelated_worktree_changes(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(head, status=" M README.md\n")

        with self.assertRaisesRegex(artefacts_cli.PublishError, "working tree"):
            self.publish(repo, source, runner)

    def test_publish_rejects_non_main_branch(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(head, branch="feature")

        with self.assertRaisesRegex(artefacts_cli.PublishError, "branch main"):
            self.publish(repo, source, runner)

    def test_publish_rejects_diverged_main(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(head, divergence="0\t1\n")

        with self.assertRaisesRegex(artefacts_cli.PublishError, "up to date"):
            self.publish(repo, source, runner)

    def test_publish_no_change_exits_before_confirmation_or_branch(self):
        repo, source, head = self.make_repository(changed=False)
        runner = RecordingRunner(head)

        result = self.publish(
            repo,
            source,
            runner,
            lambda _: self.fail("confirmation should not be requested"),
        )

        self.assertIsNone(result)
        self.assertFalse(runner.called(["git", "switch"]))

    def test_publish_treats_an_orphan_alone_as_a_change(self):
        repo, source, head = self.make_repository(changed=False)
        (repo / "artefacts" / "images" / "stale.png").write_bytes(b"stale")
        runner = RecordingRunner(
            head,
            pages=[{"status": "built", "commit": "merge123", "error": {"message": None}}],
        )

        result = self.publish(repo, source, runner)

        self.assertEqual(result.merge_commit, "merge123")
        self.assertFalse((repo / "artefacts" / "images" / "stale.png").exists())

    def test_publish_cancellation_does_not_create_branch(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(head)

        result = self.publish(repo, source, runner, lambda _: "no")

        self.assertIsNone(result)
        self.assertFalse(runner.called(["git", "switch"]))

    def test_publish_never_merges_when_expected_check_is_missing(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(head, checks=[])

        with self.assertRaisesRegex(artefacts_cli.PublishError, "validate check is missing"):
            self.publish(repo, source, runner)

        self.assertFalse(runner.called(["gh", "pr", "merge"]))

    def test_publish_waits_for_expected_check_to_appear(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(
            head,
            check_responses=[[], [{"name": "validate", "bucket": "pass"}]],
        )

        result = self.publish(repo, source, runner)

        self.assertEqual(result.merge_commit, "merge123")
        json_checks = [
            command
            for command in runner.commands
            if command[:3] == ["gh", "pr", "checks"] and "--json" in command
        ]
        self.assertGreaterEqual(len(json_checks), 3)

    def test_publish_never_merges_when_check_fails(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(head, watch_returncode=1)

        with self.assertRaisesRegex(artefacts_cli.PublishError, "checks failed"):
            self.publish(repo, source, runner)

        self.assertFalse(runner.called(["gh", "pr", "merge"]))

    def test_publish_rejects_pages_error(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(
            head,
            pages=[{"status": "errored", "commit": "merge123", "error": {"message": "build failed"}}],
        )

        with self.assertRaisesRegex(artefacts_cli.PublishError, "build failed"):
            self.publish(repo, source, runner)

    def test_publish_ignores_pages_error_for_older_commit(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(
            head,
            pages=[
                {
                    "status": "errored",
                    "commit": "older",
                    "error": {"message": "old failure"},
                },
                {
                    "status": "built",
                    "commit": "merge123",
                    "error": {"message": None},
                },
            ],
        )

        result = self.publish(repo, source, runner)

        self.assertEqual(result.merge_commit, "merge123")

    def test_publish_rejects_non_200_public_url(self):
        repo, source, head = self.make_repository()
        bad_url = "https://kevinlin.github.io/artefacts/images/card.png"
        runner = RecordingRunner(head, bad_url=bad_url)

        with self.assertRaisesRegex(artefacts_cli.PublishError, "HTTP 404"):
            self.publish(repo, source, runner)

    def test_publish_success_returns_urls_and_waits_for_merge_commit(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(
            head,
            pages=[
                {"status": "built", "commit": "older", "error": {"message": None}},
                {"status": "built", "commit": "merge123", "error": {"message": None}},
            ],
        )

        result = self.publish(repo, source, runner)

        self.assertEqual(result.pull_request_url, "https://github.com/example/site/pull/7")
        self.assertEqual(result.merge_commit, "merge123")
        self.assertEqual(result.catalogue_url, "https://kevinlin.github.io/artefacts/")
        self.assertEqual(result.verified_url_count, 3)
        self.assertIn("Update (1)", runner.pr_body)
        self.assertTrue(runner.called(["git", "switch", "-c", "agent/sync-artefacts-20260726-143000"]))
        self.assertTrue(runner.called(["git", "add", "--all", "--", "artefacts"]))
        self.assertTrue(
            runner.called(
                [
                    "gh",
                    "pr",
                    "create",
                    "--base",
                    "main",
                    "--head",
                    "agent/sync-artefacts-20260726-143000",
                    "--title",
                    "Sync published artefacts",
                    "--body-file",
                ]
            )
        )
        self.assertTrue(runner.called(["gh", "pr", "merge"]))
        merges = [
            command for command in runner.commands if command[:3] == ["gh", "pr", "merge"]
        ]
        self.assertEqual([command[-1] for command in merges], ["--merge"])

    def test_publish_returns_to_main_and_fast_forwards_after_merge(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(head)

        self.publish(repo, source, runner)

        expected = (
            ["gh", "pr", "merge"],
            ["git", "switch", "main"],
            ["git", "pull", "--ff-only"],
        )
        self.assertEqual(
            [command[:3] for command in runner.commands if command[:3] in expected],
            list(expected),
        )

    def test_publish_warns_but_finishes_when_the_fast_forward_fails(self):
        repo, source, head = self.make_repository()
        runner = RecordingRunner(
            head, failures={("git", "pull"): "diverged from origin/main"}
        )

        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            result = self.publish(repo, source, runner)

        self.assertEqual(result.merge_commit, "merge123")
        self.assertIn("cannot fast-forward main", errors.getvalue())
        self.assertTrue(runner.called(["curl"]))


if __name__ == "__main__":
    unittest.main()
