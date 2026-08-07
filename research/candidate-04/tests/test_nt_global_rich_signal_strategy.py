"""V51 compatibility entrypoint for the established global portfolio contract.

The V51 workflow originally referenced this historical test filename while the
actual contract tests live in ``test_nt_multi_asset_runtime_contract.py``.
This module deliberately re-exports that exact test class and verifies the
GitHub container can read the checked-out commit used by the evidence step.
No strategy, signal, risk, cost or market-state logic is changed.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import unittest

from test_nt_multi_asset_runtime_contract import MultiAssetNautilusContractTests


class WorkflowRepositoryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        workspace = str(Path.cwd())
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", workspace],
            check=True,
        )

    def test_checked_out_commit_is_readable_for_evidence(self) -> None:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
        ).strip()
        self.assertEqual(len(commit), 40)


__all__ = [
    "MultiAssetNautilusContractTests",
    "WorkflowRepositoryContractTests",
]


if __name__ == "__main__":
    unittest.main()
