from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from smc_ict_4.manifest import build_data_manifest, sha256_file, write_data_manifest


class ManifestTests(unittest.TestCase):
    def test_data_manifest_is_sorted_and_hashes_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "b.txt").write_text("b", encoding="utf-8")
            (root / "a.txt").write_text("a", encoding="utf-8")
            manifest = build_data_manifest(root, dataset="tiny")
            self.assertEqual([item.path for item in manifest.files], ["a.txt", "b.txt"])
            self.assertEqual(manifest.files[0].sha256, sha256_file(root / "a.txt"))
            destination = write_data_manifest(root / "manifest.json", manifest)
            self.assertTrue(destination.is_file())

    def test_empty_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_data_manifest(directory, dataset="empty")


if __name__ == "__main__":
    unittest.main()
