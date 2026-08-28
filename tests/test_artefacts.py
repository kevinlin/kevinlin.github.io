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

# The repository's own page template. Rendering tests assert against the real one,
# because a stub would prove nothing about the pages this repository publishes.
PAGE_TEMPLATE_PATH = ROOT / "artefacts" / "page-template.html"
PAGE_TEMPLATE = artefacts_cli.load_template(ROOT / "artefacts")


def install_page_template(artefacts_root: Path) -> None:
    """Give a fixture tree the control file `load_template` reads."""
    artefacts_root.mkdir(parents=True, exist_ok=True)
    (artefacts_root / "page-template.html").write_bytes(PAGE_TEMPLATE_PATH.read_bytes())


def test_site() -> "artefacts_cli.Site":
    return artefacts_cli.site_from_dict(valid_site())


TEST_FAVICON = '<link rel="icon" href="data:,">'


def valid_site() -> dict:
    return {
        "base_url": "https://example.test/artefacts/",
        "favicon": TEST_FAVICON,
        "catalogue": {"mode": "inject", "page": "index.html"},
    }


def valid_payload() -> dict:
    return {
        "version": 1,
        "site": valid_site(),
        "protected_files": ["vendor/chart.umd.min.js"],
        "ignored_sources": [],
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
        payload["entries"][0]["source"] = "Charts/Cost.docx"
        payload["entries"][0]["destination"] = "charts/cost.docx"

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

    def test_ignored_sources_default_to_empty_when_absent(self):
        payload = valid_payload()
        del payload["ignored_sources"]  # a manifest written before the field existed
        manifest = artefacts_cli.load_manifest(self.write_manifest(payload))
        self.assertEqual(manifest.ignored_sources, ())

    def test_loads_file_and_directory_ignore_rules(self):
        payload = valid_payload()
        payload["ignored_sources"] = ["Last30Days/", "fde/analysis.md"]
        manifest = artefacts_cli.load_manifest(self.write_manifest(payload))
        self.assertEqual(manifest.ignored_sources, ("Last30Days/", "fde/analysis.md"))

    def test_rejects_an_ignore_rule_that_escapes_the_source_root(self):
        payload = valid_payload()
        payload["ignored_sources"] = ["../secrets.md"]
        self.assert_manifest_error(payload, "ignored source must be a safe relative path")

    def test_rejects_a_duplicate_ignore_rule(self):
        payload = valid_payload()
        payload["ignored_sources"] = ["Last30Days/", "Last30Days/"]
        self.assert_manifest_error(payload, "duplicate ignored source")

    def test_rejects_an_ignore_rule_that_is_also_an_entry_source(self):
        payload = valid_payload()
        payload["ignored_sources"] = [payload["entries"][0]["source"]]
        self.assert_manifest_error(payload, "ignored source is also an entry source")

    def test_rejects_a_directory_rule_containing_an_entry_source(self):
        payload = valid_payload()
        payload["ignored_sources"] = ["Charts/"]
        self.assert_manifest_error(payload, "ignored source is also an entry source")

    def test_ignored_sources_survive_a_json_round_trip(self):
        payload = valid_payload()
        payload["ignored_sources"] = ["Last30Days/"]
        manifest = artefacts_cli.load_manifest(self.write_manifest(payload))
        restored = artefacts_cli.manifest_from_bytes(
            artefacts_cli.manifest_to_json(manifest), "round trip"
        )
        self.assertEqual(restored.ignored_sources, ("Last30Days/",))

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

    def scan(self, root: Path):
        """Scan with the destination repository placed where the fixtures put it."""
        return artefacts_cli.scan_source(root, root / "topic" / "kevinlin.github.io")

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
        (root / "topic" / "notes.txt").write_text("private", encoding="utf-8")
        (root / ".DS_Store").write_bytes(b"metadata")

        inventory = self.scan(root)

        self.assertEqual(inventory.approved, (PurePosixPath("topic/Chart.PNG"),))
        self.assertEqual(inventory.excluded, ((".txt", 1),))

    def test_scan_prunes_nested_repository_copy(self):
        root = self.make_source()
        nested = root / "topic" / "kevinlin.github.io"
        nested.mkdir()
        (nested / "private.png").write_bytes(b"private")
        (root / "topic" / "public.png").write_bytes(b"public")

        inventory = self.scan(root)

        self.assertEqual(inventory.approved, (PurePosixPath("topic/public.png"),))

    def test_scan_skips_symbolic_links(self):
        # A source folder is a working directory someone keeps shortcuts in. One
        # shortcut must not stop every command; the file it points at is scanned
        # under its real name anyway.
        root = self.make_source()
        target = root / "topic" / "target.png"
        target.write_bytes(b"png")
        (root / "topic" / "linked.png").symlink_to(target)

        inventory = self.scan(root)

        self.assertEqual(inventory.approved, (PurePosixPath("topic/target.png"),))

    def test_reconcile_rejects_unlisted_approved_source_and_carries_it(self):
        root = self.make_source()
        (root / "topic" / "New Chart.PNG").write_bytes(b"png")
        inventory = self.scan(root)
        manifest = self.manifest_for()

        with self.assertRaises(artefacts_cli.UnlistedSourceError) as caught:
            artefacts_cli.reconcile_inventory(manifest, inventory)

        self.assertEqual(caught.exception.unlisted, (PurePosixPath("topic/New Chart.PNG"),))
        self.assertIs(caught.exception.manifest, manifest)

    def test_reconcile_turns_missing_source_into_deletion_candidate(self):
        root = self.make_source()
        inventory = self.scan(root)
        manifest = self.manifest_for("topic/Missing.png")

        result = artefacts_cli.reconcile_inventory(manifest, inventory)

        self.assertEqual(tuple(entry.id for entry in result.missing_entries), ("item-1",))
        self.assertEqual(result.next_manifest.entries, ())

    def test_scan_reports_markdown_as_approved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Notes").mkdir()
            (root / "Notes" / "Report.md").write_text("# Report\n", encoding="utf-8")
            (root / "Notes" / "Draft.docx").write_bytes(b"binary")
            inventory = self.scan(root)
        self.assertEqual(inventory.approved, (PurePosixPath("Notes/Report.md"),))
        self.assertEqual(inventory.excluded, ((".docx", 1),))

    def inventory_of(self, *paths: str) -> "artefacts_cli.SourceInventory":
        return artefacts_cli.SourceInventory(
            approved=tuple(PurePosixPath(path) for path in paths),
            excluded=(),
        )

    def test_a_file_rule_ignores_only_that_file(self):
        inventory = self.inventory_of("fde/analysis.md", "fde/report.md")
        filtered, rules = artefacts_cli.apply_source_ignores(
            inventory, ("fde/analysis.md",)
        )
        self.assertEqual(filtered.approved, (PurePosixPath("fde/report.md"),))
        self.assertEqual(rules, (("fde/analysis.md", 1),))

    def test_a_directory_rule_ignores_the_whole_subtree(self):
        inventory = self.inventory_of(
            "Last30Days/a.md", "Last30Days/nested/b.md", "fde/report.md"
        )
        filtered, rules = artefacts_cli.apply_source_ignores(
            inventory, ("Last30Days/",)
        )
        self.assertEqual(filtered.approved, (PurePosixPath("fde/report.md"),))
        self.assertEqual(rules, (("Last30Days/", 2),))

    def test_a_directory_rule_does_not_match_a_prefix_of_a_sibling_name(self):
        # "Last30Days/" must not swallow "Last30DaysArchive/notes.md".
        inventory = self.inventory_of("Last30DaysArchive/notes.md")
        filtered, rules = artefacts_cli.apply_source_ignores(
            inventory, ("Last30Days/",)
        )
        self.assertEqual(filtered.approved, inventory.approved)
        self.assertEqual(rules, (("Last30Days/", 0),))

    def test_a_rule_matching_nothing_is_reported_with_a_zero_count(self):
        inventory = self.inventory_of("fde/report.md")
        _, rules = artefacts_cli.apply_source_ignores(inventory, ("gone/",))
        self.assertEqual(rules, (("gone/", 0),))

    def test_excluded_suffixes_are_left_alone(self):
        inventory = artefacts_cli.SourceInventory(
            approved=(PurePosixPath("fde/report.md"),), excluded=((".docx", 1),)
        )
        filtered, _ = artefacts_cli.apply_source_ignores(inventory, ("fde/",))
        self.assertEqual(filtered.approved, ())
        self.assertEqual(filtered.excluded, ((".docx", 1),))

    def test_ignored_sources_never_reach_the_unlisted_set(self):
        manifest = artefacts_cli.manifest_from_dict(
            {**valid_payload(), "ignored_sources": ["Notes/"]}
        )
        inventory = self.inventory_of("Charts/Cost.png", "Notes/Draft.md")
        filtered, _ = artefacts_cli.apply_source_ignores(
            inventory, manifest.ignored_sources
        )
        result = artefacts_cli.reconcile_inventory(manifest, filtered)
        self.assertEqual(result.missing_entries, ())

    def test_suggest_destination_maps_markdown_to_a_directory_index(self):
        self.assertEqual(
            artefacts_cli.suggest_destination(PurePosixPath("Notes/My_Report.md")),
            PurePosixPath("notes/my-report/index.html"),
        )


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

    def test_collection_left_empty_is_reused_instead_of_duplicated(self):
        payload = valid_payload()
        payload["entries"] = []

        proposal = self.propose(
            artefacts_cli.manifest_from_dict(payload), "charts/Extra.png"
        )

        self.assertEqual(proposal.collections, ())
        self.assertEqual(proposal.entries[0].collection, "charts")

    def test_new_image_collection_joins_the_existing_image_section(self):
        proposal = self.propose(
            artefacts_cli.manifest_from_dict(valid_payload()), "Travel/Map.png"
        )

        collection = proposal.collections[0]
        self.assertEqual(collection.id, "travel")
        self.assertEqual(collection.title, "Travel")
        self.assertEqual(collection.section, "Analysis")
        self.assertIsNone(collection.description)
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

    def test_markdown_only_collection_joins_the_presentation_section(self):
        # A .md publishes to a directory index.html, so _sections_by_media will read
        # the collection back as a presentation. The proposal must agree on the way
        # in, or the collection is filed under images and moves on the next run.
        proposal = self.proposal_for("my_report.md", "# Report\n")
        self.assertEqual(
            proposal.collections[0].section, artefacts_cli.PRESENTATION_SECTION
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

        desired = artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)

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

        desired = artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)

        self.assertEqual(
            desired[PurePosixPath("charts/chart/index.html")],
            test_site().favicon.encode("utf-8")
            + b'\n<script src="../../vendor/chart.js"></script>\n<p>Chart</p>\n',
        )

    def test_transform_html_injects_favicon_when_page_has_none(self):
        root, manifest, source_path = self.make_source_and_manifest(
            "Charts/Chart.html", "charts/chart/index.html"
        )
        source_path.write_text(
            "<!DOCTYPE html>\n<html>\n<head>\n<title>Chart</title>\n</head>\n</html>\n",
            encoding="utf-8",
        )

        desired = artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)
        page = desired[PurePosixPath("charts/chart/index.html")].decode("utf-8")

        self.assertIn(test_site().favicon, page)
        self.assertEqual(page.count('rel="icon"'), 1)
        self.assertLess(page.index("rel=\"icon\""), page.index("<title>"))

    def test_transform_html_injects_favicon_after_doctype_without_head(self):
        root, manifest, source_path = self.make_source_and_manifest(
            "Charts/Chart.html", "charts/chart/index.html"
        )
        source_path.write_text("<!DOCTYPE html>\n<p>Chart</p>\n", encoding="utf-8")

        desired = artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)

        self.assertEqual(
            desired[PurePosixPath("charts/chart/index.html")],
            b"<!DOCTYPE html>\n"
            + test_site().favicon.encode("utf-8")
            + b"\n<p>Chart</p>\n",
        )

    def test_transform_html_keeps_existing_favicon(self):
        root, manifest, source_path = self.make_source_and_manifest(
            "Charts/Chart.html", "charts/chart/index.html"
        )
        original = (
            "<!DOCTYPE html>\n<html>\n<head>\n"
            '<link rel="shortcut icon" href="own.ico">\n</head>\n</html>\n'
        )
        source_path.write_text(original, encoding="utf-8")

        desired = artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)

        self.assertEqual(
            desired[PurePosixPath("charts/chart/index.html")],
            original.encode("utf-8"),
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
            artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)

    def test_transform_html_adds_final_newline(self):
        root, manifest, source_path = self.make_source_and_manifest(
            "Charts/Chart.html", "charts/chart/index.html"
        )
        source_path.write_text("<p>Chart</p>", encoding="utf-8")

        desired = artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)

        self.assertEqual(
            desired[PurePosixPath("charts/chart/index.html")],
            test_site().favicon.encode("utf-8") + b"\n<p>Chart</p>\n",
        )

    def test_a_remaining_external_reference_is_published_and_reported(self):
        # Generalised from a cdnjs-only ban: any external host is the same
        # fragility, and refusing outright would block a page whose font or
        # analytics endpoint is a deliberate choice.
        root, manifest, source_path = self.make_source_and_manifest(
            "Charts/Chart.html", "charts/chart/index.html"
        )
        source_path.write_text(
            '<script src="https://cdnjs.cloudflare.com/chart.js"></script>',
            encoding="utf-8",
        )

        desired = artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)
        page = desired[PurePosixPath("charts/chart/index.html")].decode("utf-8")

        self.assertEqual(
            artefacts_cli.external_references(page),
            ((2, "https://cdnjs.cloudflare.com/chart.js"),),
        )

    def test_build_desired_files_omits_missing_sources(self):
        root, manifest, _ = self.make_source_and_manifest(
            "Images/Missing.png", "images/missing.png"
        )

        desired = artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)

        self.assertEqual(desired, {})

    def test_build_desired_files_renders_markdown_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Notes").mkdir()
            (root / "Notes" / "Report.md").write_text(
                "# Report\n\nBody.\n", encoding="utf-8"
            )
            manifest = artefacts_cli.Manifest(
                version=1,
                site=test_site(),
                protected_files=(PurePosixPath("vendor/marked.min.js"),),
                collections=(),
                entries=(markdown_entry(),),
            )
            desired = artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)
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
                site=test_site(),
                protected_files=(),
                collections=(),
                entries=(markdown_entry(),),
            )
            with self.assertRaisesRegex(
                artefacts_cli.TransformationError, "marked.min.js"
            ):
                artefacts_cli.build_desired_files(manifest, root, PAGE_TEMPLATE)


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
            markdown_entry(destination),
            text.encode("utf-8"),
            self.VENDOR,
            test_site(),
            PAGE_TEMPLATE,
        ).decode("utf-8")

    def test_page_embeds_the_source_and_extraction_recovers_it(self):
        text = "# Report\n\nBody with </script> and <!-- note -->.\n"
        page = self.render(text)
        self.assertEqual(artefacts_cli.extract_markdown(page), text)

    def test_page_escapes_the_title_and_uses_it_verbatim(self):
        page = self.render("# Report\n")
        self.assertIn("<title>Report &amp; Analysis | Artefacts</title>", page)
        self.assertIn("<h1>Report &amp; Analysis</h1>", page)

    def test_vendor_reference_depth_follows_the_destination(self):
        two_deep = self.render("# R\n", "notes/report/index.html")
        self.assertIn('src="../../vendor/marked.min.js"', two_deep)
        three_deep = self.render("# R\n", "notes/prompts/report/index.html")
        self.assertIn('src="../../../vendor/marked.min.js"', three_deep)

    def test_back_link_points_at_the_catalogue(self):
        page = self.render("# R\n", "notes/report/index.html")
        self.assertIn('href="../../"', page)

    def test_page_loads_the_parser_from_the_repository_not_a_cdn(self):
        references = artefacts_cli.external_references(self.render("# R\n"))
        self.assertNotIn(
            "cdnjs", " ".join(url for _, url in references)
        )

    def test_render_is_deterministic(self):
        text = "# Report\n\nBody.\n"
        self.assertEqual(self.render(text), self.render(text))

    def test_render_rejects_a_non_utf8_source(self):
        with self.assertRaisesRegex(
            artefacts_cli.TransformationError, "not UTF-8"
        ):
            artefacts_cli.render_markdown_page(
                markdown_entry(),
                b"\xff\xfe not utf-8",
                self.VENDOR,
                test_site(),
                PAGE_TEMPLATE,
            )

    def test_page_drops_a_lead_heading_that_repeats_the_title(self):
        # The removal runs in the browser: the embedded Markdown must stay
        # byte-exact, so the duplicate cannot be stripped from the source.
        page = self.render("# Report & Analysis\n\nBody.\n")
        self.assertIn("lead.remove()", page)
        self.assertEqual(
            artefacts_cli.extract_markdown(page), "# Report & Analysis\n\nBody.\n"
        )

    def test_page_finds_the_lead_heading_past_a_preamble(self):
        # Several sources open with a banner line before their H1, so the first
        # element is a paragraph. Anchoring on firstElementChild missed those and
        # printed the title twice.
        page = self.render("Banner line\n\n# Report & Analysis\n\nBody.\n")
        self.assertIn("body.querySelector('h1')", page)
        self.assertNotIn("firstElementChild", page)

    def test_page_ends_with_a_newline(self):
        self.assertTrue(self.render("# R\n").endswith("\n"))

    def test_vendor_lookup_finds_the_registered_parser(self):
        manifest = artefacts_cli.Manifest(
            version=1,
            site=test_site(),
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
            site=test_site(),
            protected_files=(PurePosixPath("vendor/chart.umd.min.js"),),
            collections=(),
            entries=(),
        )
        with self.assertRaisesRegex(
            artefacts_cli.TransformationError, "marked.min.js"
        ):
            artefacts_cli.markdown_vendor_path(manifest)


class MarkdownDiffTests(unittest.TestCase):
    def page(self, text: str) -> bytes:
        return artefacts_cli.render_markdown_page(
            markdown_entry(),
            text.encode("utf-8"),
            PurePosixPath("vendor/marked.min.js"),
            test_site(),
            PAGE_TEMPLATE,
        )

    def test_diff_reports_changed_lines_only(self):
        diff = artefacts_cli.markdown_diff(
            self.page("# Title\n\nOld body.\n"), self.page("# Title\n\nNew body.\n")
        )
        self.assertIn("-Old body.", diff)
        self.assertIn("+New body.", diff)
        self.assertNotIn("-# Title", diff)

    def test_identical_markdown_produces_no_diff(self):
        text = "# Title\n\nBody.\n"
        self.assertEqual(artefacts_cli.markdown_diff(self.page(text), self.page(text)), "")

    def test_diff_is_truncated_with_the_remaining_count(self):
        old = "# Title\n\n" + "".join(f"old line {n}\n" for n in range(60))
        new = "# Title\n\n" + "".join(f"new line {n}\n" for n in range(60))
        diff = artefacts_cli.markdown_diff(self.page(old), self.page(new), limit=10)
        lines = diff.splitlines()
        self.assertEqual(len(lines), 11)
        self.assertRegex(lines[-1], r"^… truncated, \d+ more lines$")

    def test_unextractable_page_reports_the_diff_as_unavailable(self):
        diff = artefacts_cli.markdown_diff(
            b"<html>hand written</html>", self.page("# New\n")
        )
        self.assertIn("diff unavailable", diff)

    def test_missing_published_page_produces_no_diff(self):
        self.assertEqual(artefacts_cli.markdown_diff(None, self.page("# New\n")), "")


class CatalogueTests(unittest.TestCase):
    def catalogue_payload(self):
        return {
            "version": 1,
            "site": valid_site(),
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

    def catalogue_manifest(self):
        return artefacts_cli.manifest_from_dict(self.catalogue_payload())

    def test_render_catalogue_orders_sections_and_escapes_text(self):
        rendered = artefacts_cli.render_catalogue(self.catalogue_manifest())

        self.assertLess(rendered.index("Analysis"), rendered.index("Collections"))
        self.assertIn("Images &amp; icons", rendered)
        self.assertIn("Image &lt;references&gt;.", rendered)
        self.assertIn("Card &lt;image&gt;", rendered)

    def dated_manifest(self, **dates: str):
        """One collection whose entries carry the given manifest dates."""
        return artefacts_cli.manifest_from_dict(
            {
                "version": 1,
                "site": valid_site(),
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
                        "id": identifier,
                        "source": f"Images/{identifier}.png",
                        "destination": f"images/{identifier}.png",
                        "title": identifier.title(),
                        "collection": "images",
                        "order": order,
                        "replacements": {},
                        **({"date": dates[identifier]} if identifier in dates else {}),
                    }
                    for identifier, order in (("old", 10), ("new", 20))
                ],
            }
        )

    def test_render_catalogue_links_html_directories_and_images_once(self):
        rendered = artefacts_cli.render_catalogue(self.catalogue_manifest())

        self.assertEqual(rendered.count('href="charts/chart/"'), 1)
        self.assertEqual(rendered.count('href="images/card.png"'), 1)
        self.assertNotIn("vendor/chart.umd.min.js", rendered)

    def test_render_catalogue_shows_the_newest_entry_date_per_card(self):
        rendered = artefacts_cli.render_catalogue(
            self.dated_manifest(old="2026-01-05", new="2026-03-11")
        )

        self.assertEqual(rendered.count('class="card-updated"'), 1)
        self.assertIn('<time datetime="2026-03-11">2026-03-11</time>', rendered)
        self.assertNotIn("2026-01-05", rendered)

    def sorting_manifest(self, **dates: str):
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
                **({"date": dates[identifier]} if identifier in dates else {}),
            }
            for identifier in ("first", "second", "third")
        ]
        return artefacts_cli.manifest_from_dict(
            {
                "version": 1,
                "site": valid_site(),
                "protected_files": [],
                "collections": collections,
                "entries": entries,
            }
        )

    def test_render_catalogue_sorts_cards_by_newest_date_first(self):
        rendered = artefacts_cli.render_catalogue(
            self.sorting_manifest(
                first="2026-01-05", second="2026-06-30", third="2026-03-11"
            )
        )

        self.assertLess(rendered.index("Second"), rendered.index("Third"))
        self.assertLess(rendered.index("Third"), rendered.index("First"))

    def test_render_catalogue_sorts_undated_cards_last_by_declared_order(self):
        rendered = artefacts_cli.render_catalogue(self.sorting_manifest(third="2026-03-11"))

        self.assertLess(rendered.index("Third"), rendered.index("First"))
        self.assertLess(rendered.index("First"), rendered.index("Second"))

    def test_render_catalogue_omits_the_date_without_entry_dates(self):
        rendered = artefacts_cli.render_catalogue(self.catalogue_manifest())

        self.assertNotIn("card-updated", rendered)

    def test_render_catalogue_omits_a_card_description_it_does_not_have(self):
        payload = json.loads(json.dumps(self.catalogue_payload()))
        for collection in payload["collections"]:
            collection.pop("description")
        rendered = artefacts_cli.render_catalogue(
            artefacts_cli.manifest_from_dict(payload)
        )

        self.assertNotIn("<p>", rendered)
        self.assertIn("<h3>Charts</h3>", rendered)

    def test_stamp_dates_reads_mtime_for_undated_and_leaves_a_set_date(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        source = Path(directory.name)
        (source / "Images").mkdir()
        (source / "Images" / "old.png").write_bytes(b"png")
        (source / "Images" / "new.png").write_bytes(b"png")
        os.utime(source / "Images" / "old.png", (1767225600, 1767225600))

        stamped = artefacts_cli.stamp_dates(
            self.dated_manifest(new="2026-03-11"), source, set()
        )

        dates = {entry.id: entry.date for entry in stamped.entries}
        self.assertEqual(
            dates["old"], datetime.fromtimestamp(1767225600).strftime("%Y-%m-%d")
        )
        self.assertEqual(dates["new"], "2026-03-11")

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
        install_page_template(artefacts)

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
                ("update", "index.html"),
                ("update", "manifest.json"),
            },
        )

    def test_plan_redates_a_republished_entry_and_leaves_an_unchanged_one(self):
        repo, source, manifest_path, _head = self.make_fixture()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["entries"][0]["date"] = "2020-01-01"   # Existing.png: source differs
        payload["entries"][1]["date"] = "2020-02-02"   # New.png: published as-is below
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (repo / "artefacts" / "charts" / "new.png").write_bytes(b"new")
        os.utime(source / "Charts" / "Existing.png", (1767225600, 1767225600))
        head_manifest = manifest_path.read_bytes()

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        dates = {entry.id: entry.date for entry in plan.next_manifest.entries}
        self.assertEqual(
            dates["existing"], datetime.fromtimestamp(1767225600).strftime("%Y-%m-%d")
        )
        self.assertEqual(dates["new"], "2020-02-02")

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
        self.assertIn("RENUMBERED ORDER (1)\n  new: 10 -> 20", artefacts_cli.format_plan(plan))

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
        payload["entries"][0]["id"] = "renamed"
        payload["entries"][0]["destination"] = "charts/renamed.png"
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        changes = {(change.kind, change.destination.as_posix()) for change in plan.changes}
        self.assertIn(("add", "charts/renamed.png"), changes)
        self.assertIn(("delete", "charts/existing.png"), changes)

        artefacts_cli.apply_plan(plan, repo / "artefacts")

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
        payload["entries"][0]["id"] = "renamed"
        payload["entries"][0]["destination"] = "charts/renamed.png"
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        changes = {(change.kind, change.destination.as_posix()) for change in plan.changes}
        self.assertNotIn(("delete", "charts/existing.png"), changes)

        artefacts_cli.apply_plan(plan, repo / "artefacts")

        self.assertEqual((repo / "artefacts/charts/existing.png").read_bytes(), b"old")

    def test_broken_desired_reference_fails_without_mutating_repository(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["entries"][0].update(
            {
                "id": "existing-page",
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

        artefacts_cli.apply_plan(plan, repo / "artefacts")

        self.assertEqual((repo / "artefacts/charts/existing.png").read_bytes(), b"updated")
        self.assertEqual((repo / "artefacts/charts/new.png").read_bytes(), b"new")
        self.assertFalse((repo / "artefacts/charts/removed.png").exists())
        self.assertEqual((repo / "artefacts/vendor/chart.js").read_bytes(), b"vendor")
        self.assertEqual((repo / "artefacts/notes.txt").read_text(), "keep")
        applied_manifest = artefacts_cli.load_manifest(manifest_path)
        self.assertEqual([entry.id for entry in applied_manifest.entries], ["existing", "new"])
        catalogue = (repo / "artefacts/index.html").read_text()
        self.assertIn('href="charts/new.png"', catalogue)
        self.assertNotIn("Removed", catalogue)
        self.assertFalse(any(repo.rglob("*.tmp")))

    def test_orphans_are_warned_about_and_never_deleted(self):
        # An unmanaged file may be a hand-written page or a redirect. Protected files
        # and editor droppings are not orphans at all.
        repo, source, manifest_path, head_manifest = self.make_fixture()
        (repo / "artefacts" / ".DS_Store").write_bytes(b"metadata")

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        orphans = {note.where for note in plan.notes if note.kind == "orphan"}
        self.assertEqual(orphans, {"artefacts/notes.txt"})
        self.assertEqual(
            [change for change in plan.changes if change.kind not in {"add", "update", "delete"}],
            [],
        )

        artefacts_cli.apply_plan(plan, repo / "artefacts")

        self.assertEqual((repo / "artefacts/notes.txt").read_text(), "keep")
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
        orphans = {note.where for note in plan.notes if note.kind == "orphan"}
        self.assertIn(("add", "charts/existing.png"), changes)
        self.assertIn("artefacts/renamed/existing.png", orphans)
        self.assertIn("artefacts/renamed/removed.png", orphans)

        artefacts_cli.apply_plan(plan, artefacts_root)

        # The published tree reconverges on the manifest; the hand-renamed copies are
        # reported and left for a person to remove.
        self.assertEqual((artefacts_root / "charts/existing.png").read_bytes(), b"updated")
        self.assertTrue((renamed / "existing.png").is_file())

    def test_the_sync_control_file_is_neither_an_orphan_nor_link_checked(self):
        """page-template.html steers the sync instead of being published by it."""
        repo, source, manifest_path, head_manifest = self.make_fixture()
        artefacts_root = repo / "artefacts"
        nested = artefacts_root / "charts" / "page-template.html"
        nested.write_text("$title", encoding="utf-8")

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, artefacts_root, head_manifest
        )

        orphans = {note.where for note in plan.notes if note.kind == "orphan"}
        self.assertNotIn("artefacts/page-template.html", orphans)
        # Only the control file at the root is exempt; a nested one is still a file
        # the manifest has to explain.
        self.assertIn("artefacts/charts/page-template.html", orphans)

    def test_orphan_set_matches_repository_validation_rejection(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        artefacts_root = repo / "artefacts"
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, artefacts_root, head_manifest
        )
        published, _ = artefacts_cli.scan_published_tree(artefacts_root)
        manifest = artefacts_cli.load_manifest(manifest_path)
        expected = {
            *(PurePosixPath(name) for name in artefacts_cli.CONTROL_FILES),
            *manifest.protected_files,
            *(entry.destination for entry in manifest.entries),
        }

        unexpected = artefacts_cli.unexpected_published_files(published, expected)

        self.assertEqual(
            [note.where for note in plan.notes if note.kind == "orphan"],
            [f"artefacts/{path.as_posix()}" for path in unexpected],
        )

    def test_apply_sweeps_orphan_without_touching_published_files(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        artefacts_root = repo / "artefacts"
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, artefacts_root, head_manifest
        )

        artefacts_cli.apply_plan(plan, artefacts_root)
        report = artefacts_cli.validate_repository(repo, None)

        self.assertEqual(report.entry_count, 2)
        # The orphan survives apply and validate, and is reported by both.
        self.assertEqual(
            {note.where for note in report.notes if note.kind == "orphan"},
            {"artefacts/notes.txt"},
        )
        applied = self.snapshot(artefacts_root)
        self.assertEqual(
            set(applied),
            {
                "manifest.json",
                "index.html",
                "page-template.html",
                "notes.txt",
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
            repo / "artefacts",
            lambda _: "no",
        )

        self.assertFalse(applied)
        self.assertEqual(self.snapshot(repo), before)

    def test_atlas_rebuild_runs_only_when_the_manifest_protects_an_atlas(self):
        calls = []

        def runner(args, cwd):
            calls.append(args)
            return artefacts_cli.CommandResult("2 panels packed\n", "", 0)

        manifest = artefacts_cli.manifest_from_dict(valid_payload())
        artefacts_cli.rebuild_showcase_atlas(manifest, Path("/repo"), runner)
        self.assertEqual(calls, [])

        payload = valid_payload()
        payload["protected_files"].append("showcase/atlas.js")
        with contextlib.redirect_stdout(io.StringIO()):
            artefacts_cli.rebuild_showcase_atlas(
                artefacts_cli.manifest_from_dict(payload), Path("/repo"), runner
            )
        self.assertEqual(len(calls), 1)
        self.assertIn(artefacts_cli.ATLAS_SCRIPT, calls[0])

    def test_atlas_rebuild_failure_stops_the_run(self):
        payload = valid_payload()
        payload["protected_files"].append("showcase/atlas.js")
        manifest = artefacts_cli.manifest_from_dict(payload)

        with self.assertRaises(artefacts_cli.ArtefactError):
            artefacts_cli.rebuild_showcase_atlas(
                manifest,
                Path("/repo"),
                lambda args, cwd: artefacts_cli.CommandResult("", "no ffmpeg", 1),
            )

    def test_format_plan_lists_each_change_kind_and_excluded_types(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        (source / "notes.txt").write_text("private", encoding="utf-8")
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        output = artefacts_cli.format_plan(plan)

        self.assertIn("NEW PUBLIC URLS (1)", output)
        self.assertIn("https://example.test/artefacts/charts/new.png", output)
        self.assertIn("CHANGED (3)", output)
        self.assertIn("WILL START 404-ING (1)", output)
        self.assertIn("https://example.test/artefacts/charts/removed.png", output)
        self.assertIn(".txt           1 file, unsupported type", output)
        # The orphaned file in the published tree is reported, never deleted.
        self.assertIn("orphan    artefacts/notes.txt", output)
        self.assertTrue((repo / "artefacts" / "notes.txt").is_file())

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
                test_site(),
                PAGE_TEMPLATE,
            )
        )
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, artefacts, manifest_path.read_bytes()
        )
        diffs = {
            change.destination: change.diff
            for change in plan.changes
            if change.diff is not None
        }
        self.assertIn(PurePosixPath("charts/report/index.html"), diffs)
        body = diffs[PurePosixPath("charts/report/index.html")]
        self.assertIn("-First version.", body)
        self.assertIn("+Second version.", body)
        rendered = artefacts_cli.format_plan(plan)
        self.assertIn("-First version.", rendered)
        self.assertIn("https://example.test/artefacts/charts/report/", rendered)

    def test_plan_without_markdown_reports_no_markdown_changes(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )
        self.assertEqual(
            tuple(change for change in plan.changes if change.diff is not None), ()
        )

    def test_an_ignored_source_neither_blocks_nor_publishes(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        (source / "Notes").mkdir()
        (source / "Notes" / "Working.md").write_text("# Draft\n", encoding="utf-8")

        # Without a rule the unlisted working file aborts the run.
        with self.assertRaises(artefacts_cli.UnlistedSourceError):
            artefacts_cli.create_sync_plan(
                manifest_path, source, repo / "artefacts", head_manifest
            )

        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["ignored_sources"] = ["Notes/"]
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )

        self.assertEqual(plan.ignored, (("Notes/", 1),))
        self.assertNotIn(
            PurePosixPath("notes/working/index.html"), plan.desired_files
        )
        rendered = artefacts_cli.format_plan(plan)
        self.assertIn("Notes/         1 file, matched an ignored source rule", rendered)

    def test_a_plan_without_ignore_rules_omits_the_heading(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        plan = artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head_manifest
        )
        self.assertEqual(plan.ignored, ())
        self.assertNotIn("Ignored sources", artefacts_cli.format_plan(plan))

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
            ("add", PurePosixPath("charts/report/index.html")),
            [(change.kind, change.destination) for change in first.changes],
        )
        self.assertEqual(
            tuple(change for change in first.changes if change.diff is not None), ()
        )
        artefacts_cli.apply_plan(first, artefacts, source)
        artefacts_cli.validate_repository(repo, None)

        report.write_text("# Report\n\nSecond version.\n", encoding="utf-8")
        second = artefacts_cli.create_sync_plan(manifest_path, source, artefacts, head)
        self.assertIn(
            ("update", PurePosixPath("charts/report/index.html")),
            [(change.kind, change.destination) for change in second.changes],
        )
        body = {
            change.destination: change.diff
            for change in second.changes
            if change.diff is not None
        }[PurePosixPath("charts/report/index.html")]
        self.assertIn("-First version.", body)
        self.assertIn("+Second version.", body)
        artefacts_cli.apply_plan(second, artefacts, source)
        artefacts_cli.validate_repository(repo, None)

        page = (artefacts / "charts" / "report" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            artefacts_cli.extract_markdown(page), "# Report\n\nSecond version.\n"
        )


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

    def make_fixture_with_renamed_source(self):
        """A Markdown entry whose source came back as HTML under the same name."""
        repo, source, manifest_path, _ = self.make_fixture()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["entries"].append(
            {
                "id": "doc",
                "source": "Charts/Doc.md",
                "destination": "charts/doc/index.html",
                "title": "Doc",
                "collection": "charts",
                "order": 40,
                "replacements": {},
            }
        )
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (source / "Charts" / "Doc.html").write_text("<p>doc</p>\n", encoding="utf-8")
        return repo, source, manifest_path

    def test_renamed_source_replaces_the_stale_entry(self):
        repo, source, manifest_path = self.make_fixture_with_renamed_source()

        code = self.run_command("apply", repo, source)

        self.assertEqual(code, 3)
        manifest = artefacts_cli.load_manifest(manifest_path)
        sources = [entry.source for entry in manifest.entries]
        self.assertIn(PurePosixPath("Charts/Doc.html"), sources)
        self.assertNotIn(PurePosixPath("Charts/Doc.md"), sources)
        self.assertEqual(
            [
                entry.destination
                for entry in manifest.entries
                if entry.destination == PurePosixPath("charts/doc/index.html")
            ],
            [PurePosixPath("charts/doc/index.html")],
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
        install_page_template(artefacts)
        payload = {
            "version": 1,
            "site": valid_site(),
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

    def test_validate_reports_an_unexpected_published_file_without_rejecting_it(self):
        repo = self.valid_repository()
        (repo / "artefacts" / "unlisted.png").write_bytes(b"png")

        report = artefacts_cli.validate_repository(repo, None)

        self.assertEqual(
            [note.where for note in report.notes if note.kind == "orphan"],
            ["artefacts/unlisted.png"],
        )
        self.assertTrue((repo / "artefacts" / "unlisted.png").is_file())

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

    def test_validate_reports_an_external_reference(self):
        repo = self.valid_repository()
        page = repo / "artefacts" / "charts" / "chart" / "index.html"
        page.write_text(
            '<script src="https://cdnjs.cloudflare.com/chart.js"></script>\n',
            encoding="utf-8",
        )

        report = artefacts_cli.validate_repository(repo, None)

        self.assertIn(
            artefacts_cli.external_note(
                "artefacts/charts/chart/index.html:1",
                "https://cdnjs.cloudflare.com/chart.js",
            ),
            report.notes,
        )

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
        install_page_template(repo / "artefacts")
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
        (source / "Images" / "Card.png").write_bytes(b"seed")
        # Written the way a sync would leave it — dates stamped, keys in canonical
        # order — so an unchanged tree really does plan as unchanged.
        manifest = artefacts_cli.stamp_dates(
            artefacts_cli.load_manifest(manifest_path), source, set()
        )
        manifest_path.write_bytes(artefacts_cli.manifest_to_json(manifest))
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
                    artefacts_cli.stamp_dates(manifest, source, set())
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

    def test_publish_reports_an_orphan_and_stops_when_nothing_else_changed(self):
        # An orphan is a warning, not a change, so it cannot by itself justify a
        # branch, a pull request, and a deletion of somebody's hand-written file.
        repo, source, head = self.make_repository(changed=False)
        (repo / "artefacts" / "images" / "stale.png").write_bytes(b"stale")
        runner = RecordingRunner(
            head,
            pages=[{"status": "built", "commit": "merge123", "error": {"message": None}}],
        )

        result = self.publish(repo, source, runner)

        self.assertIsNone(result)
        self.assertFalse(runner.called(["git", "switch"]))
        self.assertTrue((repo / "artefacts" / "images" / "stale.png").exists())

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
        self.assertIn("CHANGED (1)", runner.pr_body)
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


class IgnoreRuleTests(unittest.TestCase):
    """Matching used to be exact-string or literal `dir/` prefix, which published the
    files a seeded glob rule was meant to hide."""

    def assert_ignored(self, source: str, rule: str, expected: bool) -> None:
        self.assertEqual(
            artefacts_cli._is_ignored(PurePosixPath(source), (rule,)),
            expected,
            f"{rule!r} against {source!r}",
        )

    def test_a_glob_matches_the_full_path_and_the_file_name(self):
        self.assert_ignored("fde/report.local.md", "*.local.*", True)
        self.assert_ignored("fde/notes.md", "fde/*.md", True)
        self.assert_ignored("fde/notes.md", "*.local.*", False)

    def test_a_leading_dot_glob_covers_hidden_entries_at_the_source_root(self):
        self.assert_ignored(".firecrawl/page.md", ".*", True)
        self.assert_ignored("notes/page.md", ".*", False)
        # A hidden directory nested under a visible one is not covered: the glob is
        # anchored at the source root. Hide one with an explicit ".cache/" rule.
        self.assert_ignored("notes/.cache/page.md", ".*", False)
        self.assert_ignored("notes/.cache/page.md", ".cache/", True)

    def test_a_bare_directory_rule_matches_that_directory_at_any_depth(self):
        self.assert_ignored("topic/prompts/one.md", "prompts/", True)
        self.assert_ignored("prompts/one.md", "prompts/", True)
        self.assert_ignored("topic/prompts.md", "prompts/", False)

    def test_a_nested_directory_rule_still_anchors_at_the_root(self):
        self.assert_ignored("fde/prompts/one.md", "fde/prompts/", True)
        self.assert_ignored("other/fde/prompts/one.md", "fde/prompts/", False)


class SvgValidationTests(unittest.TestCase):
    """Rejected and named by line, never sanitised: a stdlib sanitiser that misses
    foreignObject or a CSS url() is worse than none, because it is then trusted."""

    CLEAN = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>\n'

    def test_a_clean_svg_passes(self):
        artefacts_cli.validate_svg(self.CLEAN, "clean.svg")

    def assert_rejected(self, body: str, reason: str, line: int) -> None:
        data = f'<svg xmlns="http://www.w3.org/2000/svg">\n{body}\n</svg>\n'.encode("utf-8")
        with self.assertRaises(artefacts_cli.ValidationError) as caught:
            artefacts_cli.validate_svg(data, "dirty.svg")
        message = str(caught.exception)
        self.assertIn(f"dirty.svg:{line}: {reason}", message)
        self.assertIn("must not contain scripts", message)

    def test_it_names_the_line_of_each_rejected_construct(self):
        self.assert_rejected("<script>alert(1)</script>", "script element", 2)
        self.assert_rejected("<foreignObject/>", "foreignObject element", 2)
        self.assert_rejected('<rect onload="x()"/>', "event handler attribute", 2)
        self.assert_rejected('<image href="//evil.test/a.png"/>', "external reference", 2)
        self.assert_rejected('<a xlink:href="javascript:x()"/>', "javascript: url", 2)
        self.assert_rejected('<image xlink:href="data:image/png;base64,AA"/>', "data: url", 2)
        self.assert_rejected('<style>@import url(//evil.test/a.css);</style>', "external css url()", 2)

    def test_it_reports_a_non_utf8_file_rather_than_guessing(self):
        with self.assertRaisesRegex(artefacts_cli.ValidationError, "not valid UTF-8"):
            artefacts_cli.validate_svg(b"\xff\xfe<svg/>", "broken.svg")


class SourceTextNormalisationTests(unittest.TestCase):
    def test_crlf_and_lone_cr_become_lf_with_a_final_newline(self):
        text = artefacts_cli.normalise_source_text(b"a\r\nb\rc", "notes.md")
        self.assertEqual(text, "a\nb\nc\n")

    def test_an_already_normalised_source_is_unchanged(self):
        self.assertEqual(artefacts_cli.normalise_source_text(b"a\nb\n", "x.md"), "a\nb\n")

    def test_a_non_utf8_source_names_the_file(self):
        with self.assertRaisesRegex(artefacts_cli.TransformationError, "notes.md: not UTF-8"):
            artefacts_cli.normalise_source_text(b"\xff\xfe", "notes.md")

    def test_a_crlf_markdown_source_still_round_trips(self):
        # With core.autocrlf=input git stores LF, so a page keeping its CRs is not the
        # page that gets committed and a fresh clone never converges.
        entry = markdown_entry()
        rendered = artefacts_cli.render_markdown_page(
            entry,
            b"# Report\r\n\r\nBody.\r\n",
            PurePosixPath("vendor/marked.min.js"),
            test_site(),
            PAGE_TEMPLATE,
        )
        artefacts_cli.verify_markdown_round_trip(
            b"# Report\r\n\r\nBody.\r\n", rendered, "Notes/Report.md"
        )
        self.assertEqual(
            artefacts_cli.extract_markdown(rendered.decode("utf-8")),
            "# Report\n\nBody.\n",
        )

    def test_a_page_missing_its_block_fails_the_round_trip(self):
        with self.assertRaisesRegex(artefacts_cli.ValidationError, "no markdown block"):
            artefacts_cli.verify_markdown_round_trip(
                b"# R\n", b"<html>hand written</html>", "Notes/Report.md"
            )


class PublishedInvariantTests(unittest.TestCase):
    """A destination and a title are frozen once published: a bookmark, an inbound
    link and a search result all point at the old one."""

    def head_bytes(self, **overrides) -> bytes:
        payload = valid_payload()
        payload["entries"][0].update(overrides)
        return (json.dumps(payload, indent=2) + "\n").encode("utf-8")

    def current(self, **overrides):
        payload = valid_payload()
        payload["entries"][0].update(overrides)
        return artefacts_cli.manifest_from_dict(payload)

    def test_an_unchanged_entry_passes(self):
        artefacts_cli.check_published_invariants(self.current(), self.head_bytes())

    def test_a_changed_destination_is_refused(self):
        with self.assertRaisesRegex(artefacts_cli.ManifestError, "would break the published URL"):
            artefacts_cli.check_published_invariants(
                self.current(destination="charts/moved.png"), self.head_bytes()
            )

    def test_a_changed_title_is_refused(self):
        with self.assertRaisesRegex(artefacts_cli.ManifestError, "never re-titled"):
            artefacts_cli.check_published_invariants(
                self.current(title="Cost analysis"), self.head_bytes()
            )

    def test_a_new_entry_is_not_constrained(self):
        payload = valid_payload()
        payload["entries"].append(second_entry())
        artefacts_cli.check_published_invariants(
            artefacts_cli.manifest_from_dict(payload), self.head_bytes()
        )

    def test_a_head_manifest_predating_the_site_block_still_guards(self):
        # Adoption would be impossible if a missing `site` failed the run, and
        # returning None would drop the guard on exactly that run.
        payload = valid_payload()
        payload.pop("site")
        head = (json.dumps(payload, indent=2) + "\n").encode("utf-8")

        with self.assertRaisesRegex(artefacts_cli.ManifestError, "would break the published URL"):
            artefacts_cli.check_published_invariants(
                self.current(destination="charts/moved.png"), head
            )

    def test_an_unreadable_head_manifest_is_skipped(self):
        artefacts_cli.check_published_invariants(
            self.current(destination="charts/moved.png"), b"not json"
        )
        artefacts_cli.check_published_invariants(
            self.current(destination="charts/moved.png"), None
        )


class SourceWarningTests(unittest.TestCase):
    def warnings(self, source: str, text: str | None = None):
        return artefacts_cli.source_warnings(PurePosixPath(source), text)

    def test_a_private_word_is_matched_at_any_word_boundary(self):
        # The component-prefix rule let all three of these through silently.
        for name in (
            "Client Presentation.pdf",
            "Internal Notes.html",
            "q1-internal-review.md",
            "topic/prompts/one.md",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    [note.kind for note in self.warnings(name)], ["secret"], name
                )

    def test_a_word_that_merely_contains_one_is_not_matched(self):
        self.assertEqual(self.warnings("clientele-survey.md"), [])
        self.assertEqual(self.warnings("draftsman.md"), [])

    def test_secret_shapes_are_reported_with_their_line(self):
        notes = self.warnings("notes/report.md", "clean\nAKIAIOSFODNN7EXAMPLE\n")
        self.assertEqual(
            [(note.where, note.detail) for note in notes],
            [("notes/report.md:2", "looks like an AWS access key")],
        )

    def test_a_binary_source_is_checked_by_name_alone(self):
        self.assertEqual(self.warnings("charts/cost.png", None), [])


class PortedPlanBehaviourTests(ArtefactFixture, unittest.TestCase):
    def plan(self, repo, source, manifest_path, head):
        return artefacts_cli.create_sync_plan(
            manifest_path, source, repo / "artefacts", head
        )

    def test_a_root_level_source_lands_in_a_collection_called_general(self):
        # Naming it after whichever file sorts first is arbitrary, and the arbitrary
        # run is the first one a new source folder sees.
        repo, source, manifest_path, _ = self.make_fixture()
        (source / "Loose Note.png").write_bytes(b"png")
        manifest = artefacts_cli.load_manifest(manifest_path)

        proposal = artefacts_cli.propose_manifest_additions(
            manifest, (PurePosixPath("Loose Note.png"),), source
        )

        self.assertEqual([c.id for c in proposal.collections], ["general"])
        self.assertEqual(proposal.collections[0].title, "General")

    def test_a_proposed_collection_carries_no_placeholder_description(self):
        repo, source, manifest_path, _ = self.make_fixture()
        (source / "Travel").mkdir()
        (source / "Travel" / "Map.png").write_bytes(b"map")
        manifest = artefacts_cli.load_manifest(manifest_path)

        proposal = artefacts_cli.propose_manifest_additions(
            manifest, (PurePosixPath("Travel/Map.png"),), source
        )

        self.assertIsNone(proposal.collections[0].description)
        merged = artefacts_cli.merge_manifest_proposal(manifest, proposal)
        self.assertNotIn("TODO", artefacts_cli.render_catalogue(merged))

    def test_the_widened_allowlist_publishes_pdf_webp_gif_and_svg(self):
        repo, source, manifest_path, head = self.make_fixture()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        for order, suffix in enumerate((".pdf", ".webp", ".gif", ".svg"), start=4):
            payload["entries"].append(
                {
                    "id": f"binary-{suffix.lstrip('.')}",
                    "source": f"Charts/Binary{suffix}",
                    "destination": f"charts/binary{suffix}",
                    "title": f"Binary {suffix.lstrip('.')}",
                    "collection": "charts",
                    "order": order * 10,
                    "replacements": {},
                }
            )
            body = (
                b'<svg xmlns="http://www.w3.org/2000/svg"/>\n'
                if suffix == ".svg"
                else b"binary"
            )
            (source / "Charts" / f"Binary{suffix}").write_bytes(body)
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        plan = self.plan(repo, source, manifest_path, head)

        for suffix in (".pdf", ".webp", ".gif", ".svg"):
            self.assertIn(
                PurePosixPath(f"charts/binary{suffix}"), plan.desired_files, suffix
            )

    def test_a_dirty_svg_blocks_the_plan_by_line_number(self):
        repo, source, manifest_path, head = self.make_fixture()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["entries"].append(
            {
                "id": "diagram",
                "source": "Charts/Diagram.svg",
                "destination": "charts/diagram.svg",
                "title": "Diagram",
                "collection": "charts",
                "order": 40,
                "replacements": {},
            }
        )
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (source / "Charts" / "Diagram.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg">\n<script>x()</script>\n</svg>\n',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            artefacts_cli.ValidationError, r"Charts/Diagram\.svg:2: script element"
        ):
            self.plan(repo, source, manifest_path, head)

    def test_a_missing_protected_file_blocks_the_plan(self):
        repo, source, manifest_path, head = self.make_fixture()
        (repo / "artefacts" / "vendor" / "chart.js").unlink()

        with self.assertRaisesRegex(
            artefacts_cli.ValidationError, "vendor/chart.js: missing protected file"
        ):
            self.plan(repo, source, manifest_path, head)

    def test_a_new_file_over_ten_megabytes_is_warned_about(self):
        repo, source, manifest_path, head = self.make_fixture()
        (source / "Charts" / "New.png").write_bytes(
            b"x" * (artefacts_cli.LARGE_FILE_BYTES + 1)
        )

        plan = self.plan(repo, source, manifest_path, head)

        self.assertIn(
            artefacts_cli.Note(
                "size",
                "https://example.test/artefacts/charts/new.png",
                "new public file is over 10 MB",
            ),
            plan.notes,
        )

    def test_an_extensionless_source_is_counted_under_its_own_label(self):
        repo, source, manifest_path, head = self.make_fixture()
        (source / "Makefile").write_text("all:\n", encoding="utf-8")

        plan = self.plan(repo, source, manifest_path, head)

        self.assertIn(("(no suffix)", 1), plan.excluded)
        self.assertIn("(no suffix)", artefacts_cli.format_plan(plan))

    def test_a_date_is_stamped_into_the_manifest_once_and_then_left_alone(self):
        repo, source, manifest_path, head = self.make_fixture()
        os.utime(source / "Charts" / "Existing.png", (1767225600, 1767225600))
        stamped = datetime.fromtimestamp(1767225600).strftime("%Y-%m-%d")

        plan = self.plan(repo, source, manifest_path, head)
        artefacts_cli.apply_plan(plan, repo / "artefacts", source)

        written = {e.id: e.date for e in artefacts_cli.load_manifest(manifest_path).entries}
        self.assertEqual(written["existing"], stamped)

        # A later touch does not move the date, so the catalogue cannot reorder itself
        # behind the user's back.
        os.utime(source / "Charts" / "Existing.png", (1800000000, 1800000000))
        again = self.plan(repo, source, manifest_path, manifest_path.read_bytes())
        self.assertEqual(
            {e.id: e.date for e in again.next_manifest.entries}["existing"], stamped
        )
