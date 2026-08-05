from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


class DevContainerTests(unittest.TestCase):
    def setUp(self) -> None:
        path = Path(__file__).resolve().parents[1] / ".devcontainer" / "devcontainer.json"
        self.config = json.loads(path.read_text(encoding="utf-8"))

    def test_research_image_is_pinned_by_digest(self) -> None:
        image = self.config["image"]
        self.assertRegex(
            image,
            r"^ghcr\.io/umtong/smc-ict-4-live-research@sha256:[0-9a-f]{64}$",
        )

    def test_prebuilt_environment_starts_without_install_hook(self) -> None:
        self.assertEqual(self.config["containerEnv"]["SMC4_PREBUILT_ENV"], "1")
        self.assertEqual(self.config["postCreateCommand"], "smc4 doctor")
        self.assertNotIn("postStartCommand", self.config)
        self.assertNotIn("updateContentCommand", self.config)

    def test_research_branch_source_overrides_baked_source(self) -> None:
        self.assertEqual(
            self.config["remoteEnv"]["PYTHONPATH"],
            "${containerWorkspaceFolder}/src",
        )


if __name__ == "__main__":
    unittest.main()
