from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd


MODULE_PATH = Path(__file__).resolve().parents[1] / "candidate.py"
SPEC = importlib.util.spec_from_file_location("candidate_04", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
candidate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = candidate
SPEC.loader.exec_module(candidate)


def make_frame(rows: int = 520) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=rows, freq="1min", tz="UTC")
    open_ = np.full(rows, 100.20)
    close = np.full(rows, 100.20)
    high = np.full(rows, 101.00)
    low = np.full(rows, 99.70)
    volume = np.full(rows, 100.0)
    taker_buy = np.full(rows, 50.0)

    # Causal failed auction at bar 400, displacement at 401, entry at 402.
    if rows > 402:
        open_[400], high[400], low[400], close[400] = 99.82, 99.88, 99.50, 99.76
        volume[400], taker_buy[400] = 180.0, 25.0
        open_[401], high[401], low[401], close[401] = 99.76, 100.35, 99.72, 100.30
        volume[401], taker_buy[401] = 160.0, 95.0
        open_[402], high[402], low[402], close[402] = 100.30, 101.20, 100.15, 101.05
        volume[402], taker_buy[402] = 170.0, 105.0

    frame = pd.DataFrame(
        {
            "open_time": (index.view("int64") // 1_000_000).astype("int64"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "close_time": ((index.view("int64") + 59_999_000_000) // 1_000_000).astype("int64"),
            "quote_volume": volume * close,
            "trades": np.full(rows, 100.0),
            "taker_buy_base": taker_buy,
            "taker_buy_quote": taker_buy * close,
            "ignore": np.zeros(rows),
            "timestamp": index,
            "close_timestamp": index + pd.Timedelta(seconds=59, milliseconds=999),
        },
        index=index,
    )
    return frame


class Candidate04Tests(unittest.TestCase):
    def config(self) -> object:
        return candidate.StrategyConfig.from_mapping(
            {
                "pool_lookback": 90,
                "context_lookback": 360,
                "min_pool_age": 5,
                "volume_window": 120,
                "atr_period": 30,
                "reversal_target_r_cap": 3.0,
                "reversal_min_target_r": 0.5,
                "gate_min_geom_daily": -1.0,
                "gate_min_trades": 1,
                "gate_min_active_days": 1,
                "gate_max_largest_winner_share": 1.0,
            }
        )

    def test_failed_auction_reaches_target(self) -> None:
        result = candidate.backtest_frame(make_frame(), self.config())
        trades = result["trades"]
        self.assertGreaterEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade.kind, "SWEEP_ABSORPTION_REVERSAL")
        self.assertEqual(trade.side, "LONG")
        self.assertEqual(trade.exit_reason, "TARGET_LIQUIDITY")
        self.assertGreater(trade.net_pnl, 0)
        self.assertLessEqual(result["metrics"]["max_concurrent_entry_plus_position"], 1)

    def test_stop_loss_budget_includes_costs(self) -> None:
        frame = candidate.prepare_features(make_frame(), self.config())
        intent = candidate.EntryIntent(
            scenario_id="risk-test",
            kind="SWEEP_ACCEPTANCE_CONTINUATION",
            side=1,
            entry_index=402,
            level=100.0,
            extreme=99.55,
            atr=float(frame.iloc[401]["atr"]),
            opposing_pool=None,
            confirmed_index=401,
        )
        position, rejection = candidate._position_from_intent(frame, intent, self.config(), 100_000.0)
        self.assertIsNone(rejection)
        self.assertIsNotNone(position)
        assert position is not None
        stop_fill = candidate._adverse_fill(
            position.stop_price,
            position.side,
            self.config().stop_slippage_bps,
            entry=False,
        )
        fee_rate = self.config().fee_bps / 10_000.0
        loss = position.quantity * (
            abs(position.entry_price - stop_fill)
            + position.entry_price * fee_rate
            + stop_fill * fee_rate
            + position.entry_price * (self.config().funding_bps_per_event / 10_000.0)
        )
        self.assertTrue(math.isclose(loss, 3_000.0, rel_tol=1e-9, abs_tol=1e-6))

    def test_features_do_not_change_when_future_is_appended(self) -> None:
        base = make_frame(500)
        future = make_frame(40).copy()
        future.index = pd.date_range(base.index[-1] + pd.Timedelta(minutes=1), periods=40, freq="1min", tz="UTC")
        future["timestamp"] = future.index
        future["close_timestamp"] = future.index + pd.Timedelta(seconds=59, milliseconds=999)
        future["high"] = 500.0
        future["low"] = 1.0
        combined = pd.concat([base, future])

        left = candidate.prepare_features(base, self.config())
        right = candidate.prepare_features(combined, self.config()).iloc[: len(base)]
        columns = [
            "atr",
            "volume_median",
            "pool_high",
            "pool_low",
            "context_high",
            "context_low",
            "flow_ratio",
        ]
        pd.testing.assert_frame_equal(left[columns], right[columns])


if __name__ == "__main__":
    unittest.main()
