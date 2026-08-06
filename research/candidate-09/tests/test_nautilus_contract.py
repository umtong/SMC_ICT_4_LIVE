from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run import run_nautilus_segment
from state_engine import FlowBar


HERE = Path(__file__).resolve().parents[1]
MINUTE = 60_000_000_000


class NautilusContractTest(unittest.TestCase):
    def test_native_bars_subscription_and_accounting_without_signal(self):
        config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
        bars = []
        price = 50_000.0
        for index in range(1, 301):
            wiggle = 0.5 if index % 2 else -0.5
            close = price + wiggle
            bars.append(
                FlowBar(
                    ts_ns=index * MINUTE,
                    open=price,
                    high=max(price, close) + 0.2,
                    low=min(price, close) - 0.2,
                    close=close,
                    volume=100.0,
                    taker_buy_volume=50.0,
                    trade_count=100,
                ),
            )
            price = close
        detail = run_nautilus_segment(
            config=config,
            bars=bars,
            segment="contract-no-signal",
            variant="baseline",
        )
        self.assertEqual(detail.outcome.implementation_status, "OK")
        self.assertEqual(detail.outcome.trades, 0)
        self.assertEqual(detail.outcome.missing_feature_bars, 0)
        self.assertFalse(detail.outcome.open_position_at_stop)
        self.assertAlmostEqual(detail.outcome.ending_nav, config["risk"]["starting_nav_usdt"], places=6)
        self.assertAlmostEqual(detail.outcome.accounting_error or 0.0, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
