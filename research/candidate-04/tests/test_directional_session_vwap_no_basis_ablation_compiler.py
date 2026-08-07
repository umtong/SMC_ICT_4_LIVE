from __future__ import annotations

import unittest

import directional_session_vwap_no_basis_ablation_compiler as candidate


class DirectionalSessionNoBasisAblationTests(unittest.TestCase):
    def test_only_reclaim_basis_gate_is_removed(self) -> None:
        source = candidate.BASE.read_text(encoding="utf-8")
        changed = candidate.transform_source(source)
        self.assertIn(candidate.OLD_GATE, source)
        self.assertNotIn(candidate.OLD_GATE, changed)
        self.assertIn(candidate.NEW_GATE, changed)
        self.assertEqual(
            source.count("basis = parent.side * float(candidate[\"basis_change_5m\"])") ,
            changed.count("basis = parent.side * float(candidate[\"basis_change_5m\"])") ,
        )
        self.assertIn("five-minute basis ablated", changed)

    def test_source_marker_must_be_unique(self) -> None:
        with self.assertRaises(RuntimeError):
            candidate.transform_source("no matching detector")


if __name__ == "__main__":
    unittest.main()
