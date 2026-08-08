from __future__ import annotations

import copy
import unittest

import pandas as pd

from flow_carry_logic import HOUR_NS
from flow_carry_logic import SECOND_NS
from flow_carry_logic import build_flow_carry_plan
from flow_carry_logic import is_full_utc_hour


def _rows(*, side: int = 1, minute_offset: int = 0):
    end = pd.Timestamp("2024-01-01T03:00:19.999999999Z")
    end += pd.Timedelta(minutes=minute_offset)
    count = 1_082
    start = end - pd.Timedelta(seconds=10 * (count - 1))
    result = []
    for index in range(count):
        ts = start + pd.Timedelta(seconds=10 * index)
        fraction = index / (count - 1)
        close = 100.0 + 30.0 * fraction if side > 0 else 130.0 - 30.0 * fraction
        result.append(
            {
                "ts": int(ts.value),
                "open": close - 0.05 * side,
                "high": close + 0.20,
                "low": close - 0.20,
                "close": close,
            },
        )
    return result


class FlowCarryLogicTests(unittest.TestCase):
    def test_full_hour_uses_response_clock(self) -> None:
        rows = _rows(side=1)
        self.assertTrue(is_full_utc_hour(int(rows[-1]["ts"])))
        shifted = _rows(side=1, minute_offset=15)
        self.assertFalse(is_full_utc_hour(int(shifted[-1]["ts"])))

    def test_long_plan_requires_one_and_three_hour_alignment(self) -> None:
        rows = _rows(side=1)
        decision = build_flow_carry_plan(rows, side=1)
        self.assertTrue(decision.eligible)
        self.assertIsNotNone(decision.plan)
        plan = decision.plan
        assert plan is not None
        self.assertGreater(plan.directional_return_1h, 0.0)
        self.assertGreater(plan.directional_return_3h, 0.0)
        self.assertLess(plan.stop_price, plan.entry_estimate)
        self.assertEqual(
            plan.hold_until_ns - plan.response_ts_ns,
            4 * HOUR_NS,
        )

    def test_short_plan_is_symmetric(self) -> None:
        decision = build_flow_carry_plan(_rows(side=-1), side=-1)
        self.assertTrue(decision.eligible)
        plan = decision.plan
        assert plan is not None
        self.assertGreater(plan.directional_return_1h, 0.0)
        self.assertGreater(plan.directional_return_3h, 0.0)
        self.assertGreater(plan.stop_price, plan.entry_estimate)

    def test_non_full_hour_is_rejected_without_threshold_tuning(self) -> None:
        decision = build_flow_carry_plan(
            _rows(side=1, minute_offset=15),
            side=1,
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, "NOT_FULL_UTC_HOUR")

    def test_mixed_horizon_trend_is_rejected(self) -> None:
        rows = _rows(side=1)
        current_ts = int(rows[-1]["ts"])
        target = current_ts - HOUR_NS
        index = max(
            i for i, row in enumerate(rows[:-1])
            if int(row["ts"]) <= target
        )
        rows[index] = {
            **rows[index],
            "open": 140.0,
            "high": 140.2,
            "low": 139.8,
            "close": 140.0,
        }
        decision = build_flow_carry_plan(rows, side=1)
        self.assertFalse(decision.eligible)
        self.assertEqual(
            decision.reason,
            "ONE_AND_THREE_HOUR_TREND_NOT_ALIGNED",
        )

    def test_response_bar_is_excluded_from_structural_stop(self) -> None:
        rows = _rows(side=1)
        prior_low = min(float(row["low"]) for row in rows[-361:-1])
        rows[-1] = {
            **rows[-1],
            "open": rows[-1]["close"],
            "high": rows[-1]["close"] + 0.2,
            "low": 1.0,
        }
        decision = build_flow_carry_plan(rows, side=1)
        self.assertTrue(decision.eligible)
        plan = decision.plan
        assert plan is not None
        self.assertGreater(plan.stop_price, 1.0)
        self.assertLess(plan.stop_price, prior_low)
        self.assertAlmostEqual(plan.prior_hour_low, prior_low)

    def test_future_rows_cannot_change_existing_plan(self) -> None:
        rows = _rows(side=1)
        original = build_flow_carry_plan(rows, side=1)
        future = copy.deepcopy(rows)
        last_ts = int(future[-1]["ts"])
        future.extend(
            {
                "ts": last_ts + (index + 1) * 10 * SECOND_NS,
                "open": 1_000.0,
                "high": 1_100.0,
                "low": 900.0,
                "close": 1_000.0,
            }
            for index in range(100)
        )
        replay = build_flow_carry_plan(
            [row for row in future if int(row["ts"]) <= last_ts],
            side=1,
        )
        self.assertEqual(original, replay)

    def test_insufficient_three_hour_context_closes_no_trade(self) -> None:
        rows = _rows(side=1)[-500:]
        decision = build_flow_carry_plan(rows, side=1)
        self.assertFalse(decision.eligible)
        self.assertEqual(
            decision.reason,
            "INSUFFICIENT_ONE_OR_THREE_HOUR_CONTEXT",
        )


if __name__ == "__main__":
    unittest.main()
