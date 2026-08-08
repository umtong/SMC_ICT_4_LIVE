from __future__ import annotations

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
SOURCE_ROOT = HERE.parent / "session_portfolio_v1"
for path in (HERE, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from continuous_far_materializer import materialize_continuous_far_source  # noqa: E402
from runner_materializer import materialize_runner_source  # noqa: E402


class ContinuousFarMaterializerTest(unittest.TestCase):
    def test_materializes_actual_locked_runner_fail_closed(self) -> None:
        source = (SOURCE_ROOT / "run_leadership_scdam_base.py").read_text(
            encoding="utf-8"
        )
        source = materialize_runner_source(source)
        source = materialize_continuous_far_source(source)
        compile(source, "materialized-core-far-runner.py", "exec")
        self.assertIn("DEVELOPMENT_DOMAIN_CORE_FAR_ONLY", source)
        self.assertIn("SCENARIO_MAX_HOLD_EXIT", source)
        self.assertIn("RESOLUTION_TAIL_UNRESOLVED", source)
        self.assertIn('"validation_eligible": False', source)
        self.assertNotIn(
            "if self.last_ts_ns >= self.config.evaluation_end_ns:\n"
            "                if self.buffer_ts",
            source,
        )
        self.assertNotIn("SessionAuctionBridge", source)


if __name__ == "__main__":
    unittest.main()
