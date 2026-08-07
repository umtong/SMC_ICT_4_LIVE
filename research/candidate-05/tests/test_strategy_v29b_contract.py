from __future__ import annotations

import ast
from pathlib import Path
import unittest

from strategy_v9 import ArmedEntryPath


class EncodedCandidateEntryPathContractTest(unittest.TestCase):
    FILES = (
        "strategy_v29b_external_displacement_fvg.py",
        "strategy_v30_external_acceptance_retest.py",
        "strategy_v31_impact_resiliency_reversal.py",
        "strategy_v32_queue_pressure_release.py",
    )

    def test_every_encoded_candidate_uses_current_armed_entry_path_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        expected = set(ArmedEntryPath.__dataclass_fields__)
        violations: list[str] = []
        observed = 0
        for filename in self.FILES:
            path = root / filename
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "ArmedEntryPath"
                ):
                    continue
                observed += 1
                keywords = {item.arg for item in node.keywords}
                if keywords != expected:
                    violations.append(
                        f"{filename}: expected {sorted(expected)}, got {sorted(keywords)}",
                    )
        self.assertEqual(observed, len(self.FILES))
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
