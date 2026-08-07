"""Runtime config-path propagation contract for the quote-resiliency runner."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import run_quote_resiliency_nautilus as adapter


class RuntimeConfigPathContracts(unittest.TestCase):
    def test_active_config_path_overrides_default_for_all_adapter_loaders(self) -> None:
        previous = adapter._ACTIVE_CONFIG_PATH
        with tempfile.TemporaryDirectory() as directory:
            custom = Path(directory) / "custom.json"
            custom.write_text("{}", encoding="utf-8")
            adapter._ACTIVE_CONFIG_PATH = custom
            try:
                self.assertEqual(adapter._config_path(), custom.resolve())
            finally:
                adapter._ACTIVE_CONFIG_PATH = previous

    def test_default_path_is_restored_when_no_suite_is_active(self) -> None:
        previous = adapter._ACTIVE_CONFIG_PATH
        adapter._ACTIVE_CONFIG_PATH = None
        try:
            self.assertEqual(
                adapter._config_path(),
                adapter.DEFAULT_CONFIG_PATH.resolve(),
            )
        finally:
            adapter._ACTIVE_CONFIG_PATH = previous

    def test_run_suite_sets_and_restores_config_path(self) -> None:
        source = Path(adapter.__file__).read_text(encoding="utf-8")
        self.assertIn("global _ACTIVE_ABLATION, _ACTIVE_CONFIG_PATH", source)
        self.assertIn("_ACTIVE_CONFIG_PATH = config_path.resolve()", source)
        self.assertIn("_ACTIVE_CONFIG_PATH = previous_config_path", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
