import importlib.util
import hashlib
import json
import sys
import tempfile
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

    def test_rejects_duplicate_collection_order(self):
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

        self.assert_manifest_error(payload, "duplicate collection order")

    def test_rejects_duplicate_entry_order_within_collection(self):
        payload = valid_payload()
        duplicate = second_entry()
        duplicate["order"] = 10
        payload["entries"].append(duplicate)

        self.assert_manifest_error(payload, "duplicate entry order")


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

    def test_reconcile_rejects_unlisted_approved_source_with_suggestion(self):
        root = self.make_source()
        (root / "topic" / "New Chart.PNG").write_bytes(b"png")
        inventory = artefacts_cli.scan_source(root)
        manifest = self.manifest_for()

        with self.assertRaisesRegex(
            artefacts_cli.InventoryError, "topic/new-chart.png"
        ):
            artefacts_cli.reconcile_inventory(manifest, inventory)

    def test_reconcile_turns_missing_source_into_deletion_candidate(self):
        root = self.make_source()
        inventory = artefacts_cli.scan_source(root)
        manifest = self.manifest_for("topic/Missing.png")

        result = artefacts_cli.reconcile_inventory(manifest, inventory)

        self.assertEqual(tuple(entry.id for entry in result.missing_entries), ("item-1",))
        self.assertEqual(result.next_manifest.entries, ())


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
            artefacts_cli.TransformationError, "forbidden cdnjs reference"
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


class ApplyTests(unittest.TestCase):
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

    def test_plan_calculation_does_not_mutate_repository(self):
        repo, source, manifest_path, head_manifest = self.make_fixture()
        before = self.snapshot(repo)

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
        self.assertEqual((repo / "artefacts/notes.txt").read_text(), "keep")
        applied_manifest = artefacts_cli.load_manifest(manifest_path)
        self.assertEqual([entry.id for entry in applied_manifest.entries], ["existing", "new"])
        catalogue = (repo / "artefacts/index.html").read_text()
        self.assertIn('href="charts/new.png"', catalogue)
        self.assertNotIn("Removed", catalogue)
        self.assertFalse(any(repo.rglob("*.tmp")))

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
        self.assertIn("Excluded source types: .md", output)


if __name__ == "__main__":
    unittest.main()
