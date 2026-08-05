from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import pandas as pd

CANDIDATE = Path(__file__).resolve().parents[1] / "event_time_candidate.py"
SOURCE_ROOT = CANDIDATE.with_name("event_time_source")
SPEC = importlib.util.spec_from_file_location("candidate04_event_time_test", CANDIDATE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {CANDIDATE}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class EventTimeCandidateTests(unittest.TestCase):
    def test_fragment_source_compiles(self) -> None:
        source = "".join(path.read_text(encoding="utf-8") for path in sorted(SOURCE_ROOT.glob("*.pyfrag")))
        compile(source, str(SOURCE_ROOT), "exec")

    def test_three_percent_loss_budget_includes_expected_costs(self) -> None:
        nav = 100_000.0
        entry = MODULE.entry_fill(50_000.0, side=1, bps=2)
        denominator = MODULE.denom(entry, stop=49_500.0, side=1)
        stop_fill = MODULE.exit_fill(49_500.0, side=1, bps=3)
        expected = (
            entry - stop_fill
            + 0.0005 * entry
            + 0.0005 * stop_fill
            + 0.0001 * entry
        )
        self.assertAlmostEqual(denominator, expected, places=10)
        quantity = nav * 0.03 / denominator
        self.assertAlmostEqual(quantity * denominator, nav * 0.03, places=8)

    def test_cost_adjusted_target_is_net_r(self) -> None:
        entry = 50_000.0
        denominator = MODULE.denom(entry, stop=49_500.0, side=1)
        trigger = MODULE.target_trigger(entry, denominator, side=1, R=1.35)
        start = pd.Timestamp("2024-01-01 00:01:00", tz="UTC")
        end = start + pd.Timedelta(seconds=1)
        realized = MODULE.net_r(entry, trigger, side=1, d=denominator, slip=2, t0=start, t1=end)
        self.assertAlmostEqual(realized, 1.35, places=10)

    def test_same_second_stop_and_target_uses_stop_first(self) -> None:
        index = pd.date_range("2024-01-01 00:01:00", periods=3, freq="1s", tz="UTC")
        frame = pd.DataFrame(
            {
                "open": [100.0, 100.0, 100.0],
                "high": [103.0, 100.0, 100.0],
                "low": [97.0, 100.0, 100.0],
                "close": [100.0, 100.0, 100.0],
            },
            index=index,
        )
        result = MODULE.outcome(frame, entry_i=0, side=1, stop=99.0, R=0.80, maxhold=3)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["reason"], "STOP")
        self.assertLess(result["r"], 0.0)

    def test_detector_uses_prior_only_liquidity_pool(self) -> None:
        source = "".join(path.read_text(encoding="utf-8") for path in sorted(SOURCE_ROOT.glob("*.pyfrag")))
        self.assertIn("s.high.shift(1).rolling", source)
        self.assertIn("s.low.shift(1).rolling", source)
        self.assertIn("'entry_i':j+1", source)


if __name__ == "__main__":
    unittest.main()
