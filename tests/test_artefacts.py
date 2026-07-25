import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
