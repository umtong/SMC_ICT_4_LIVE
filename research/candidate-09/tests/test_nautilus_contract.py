from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Direct unittest discovery starts from the repository root rather than this script
# directory, so load the same narrow import shim explicitly.
import sitecustomize  # noqa: F401

from run import run_nautilus_segment
from state_engine import FlowBar


class NautilusContractTest(unittest.TestCase):
    def test_engine_wrangler_subscription_and_accounting_without_signal(self) -> None:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        config = copy.deepcopy(config)
        # Variable control: prohibit any breach so this test isolates engine,
        # timestamp, account and lifecycle integration from trading logic.
        config["breach"]["minimum_breach_atr"] = 1000.0
        bars = []
        minute_ns = 60_000_000_000
        for index in range(180):
            center = 50_000.0 + 10.0 * math.sin(index / 20.0)
            bars.append(
                FlowBar(
                    ts_ns=(index + 1) * minute_ns,
                    open=center,
                    high=center + 5.0,
                    low=center - 5.0,
                    close=center + math.sin(index / 3.0),
                    volume=100.0 + index % 7,
                    taker_buy_volume=50.0,
                    trade_count=100,
                ),
            )
        detail = run_nautilus_segment(
            config=config,
            bars=bars,
            segment="contract-smoke",
            variant="baseline",
        )
        self.assertEqual(detail.outcome.trades, 0)
        self.assertEqual(detail.outcome.implementation_status, "OK")
        self.assertAlmostEqual(detail.outcome.ending_nav, detail.outcome.starting_nav)
        self.assertFalse(detail.outcome.open_position_at_stop)
        self.assertEqual(detail.outcome.missing_feature_bars, 0)


if __name__ == "__main__":
    unittest.main()
