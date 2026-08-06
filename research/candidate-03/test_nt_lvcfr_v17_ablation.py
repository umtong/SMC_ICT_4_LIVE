from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ablate_nt_lvcfr_v17_expansion import ablate_expansion
from derive_nt_lvcfr_v17_signals import SPOT_LED_OI_EXPANSION_ACCEPTANCE


class V17ExpansionAblationTests(unittest.TestCase):
    def test_only_expansion_family_is_removed_without_mutating_retained_signals(self) -> None:
        retained_a = {
            "scenario_id": "A",
            "scenario_kind": "FIRST_BREAK_CHOCH_REVERSAL",
            "confirm_time_ns": 100,
            "direction": -1,
            "initial_stop": 101.0,
            "details": {"fixed": True},
        }
        removed = {
            "scenario_id": "B",
            "scenario_kind": SPOT_LED_OI_EXPANSION_ACCEPTANCE,
            "confirm_time_ns": 200,
            "direction": 1,
            "initial_stop": 99.0,
            "details": {"fixed": True},
        }
        retained_b = {
            "scenario_id": "C",
            "scenario_kind": "MEASURED_ACCEPTANCE_CONTINUATION",
            "confirm_time_ns": 300,
            "direction": 1,
            "initial_stop": 98.0,
            "details": {"fixed": True},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            output = root / "output.json"
            manifest = root / "manifest.json"
            source.write_text(
                json.dumps([retained_a, removed, retained_b]),
                encoding="utf-8",
            )
            result = ablate_expansion(
                source_signals=source,
                output_signals=output,
                output_manifest=manifest,
            )
            self.assertEqual(result, [retained_a, retained_b])
            self.assertEqual(json.loads(output.read_text()), [retained_a, retained_b])
            metadata = json.loads(manifest.read_text())
            self.assertEqual(metadata["removed_signal_count"], 1)
            self.assertEqual(metadata["retained_signal_count"], 2)
            self.assertEqual(
                metadata["removed_variable"],
                "SPOT_LED_OI_EXPANSION_ACCEPTANCE_FAMILY",
            )

    def test_ablation_refuses_schedule_without_the_prespecified_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            source.write_text(
                json.dumps(
                    [
                        {
                            "scenario_id": "A",
                            "scenario_kind": "FIRST_BREAK_CHOCH_REVERSAL",
                            "confirm_time_ns": 100,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "removed no signals"):
                ablate_expansion(
                    source_signals=source,
                    output_signals=root / "output.json",
                    output_manifest=root / "manifest.json",
                )

    def test_ablation_source_contains_no_execution_or_pnl_engine(self) -> None:
        source = Path(__file__).with_name(
            "ablate_nt_lvcfr_v17_expansion.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertNotIn("BacktestNode", source)
        self.assertIn("UNCHANGED_NAUTILUSTRADER_EXECUTION_AND_ACCOUNTING", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
