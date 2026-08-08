from __future__ import annotations

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
CORE_ROOT = HERE.parent / "core_far_continuous_v1"
SOURCE_ROOT = HERE.parent / "session_portfolio_v1"
for path in (HERE, CORE_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from continuous_far_materializer import materialize_continuous_far_source  # noqa: E402
from runner_materializer import materialize_runner_source  # noqa: E402
from structure_transfer_materializer import (  # noqa: E402
    materialize_structure_transfer_source,
)


class StructureTransferMaterializerTest(unittest.TestCase):
    def test_materializes_locked_runner_fail_closed(self) -> None:
        source = (SOURCE_ROOT / "run_leadership_scdam_base.py").read_text(
            encoding="utf-8"
        )
        source = materialize_runner_source(source)
        source = materialize_continuous_far_source(source)
        source = materialize_structure_transfer_source(source)
        compile(source, "materialized-structure-transfer-runner.py", "exec")
        for marker in (
            "STRUCTURAL_RISK_TRANSFER_REQUESTED",
            "STRUCTURAL_RISK_TRANSFER_CONFIRMED",
            "STRUCTURAL_TRANSFER_ABLATION_BASELINE_SCENARIO_ONLY",
            "baseline_scenario_ids",
            "self.modify_order(",
            "trigger_price=instrument.make_price(rounded)",
        ):
            self.assertIn(marker, source)
        self.assertIn("engine.internal_lows", source)
        self.assertIn("engine.internal_highs", source)
        self.assertIn("event_ts_ns > self.active_position_opened_ns", source)
        self.assertIn("known_ts_ns > self.active_position_opened_ns", source)
        self.assertNotIn("fixed_mfe", source.lower())


if __name__ == "__main__":
    unittest.main()
