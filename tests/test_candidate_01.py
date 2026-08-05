from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile

import pandas as pd

CANDIDATE_DIR = Path(__file__).resolve().parents[1] / "research" / "candidate-01"
if str(CANDIDATE_DIR) not in sys.path:
    sys.path.insert(0, str(CANDIDATE_DIR))

from core import (  # noqa: E402
    AuctionBar,
    AuctionStateMachine,
    CandidateConfig,
    Response,
    Side,
)
from data import _read_archive, to_auction_bars  # noqa: E402
from nautilus_backtest import GlobalEntryGate  # noqa: E402
from seed_protocol import seeded_weeks  # noqa: E402


NS = 60_000_000_000


def bar(
    minute: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    quote: float = 1_000.0,
    buy_quote: float = 500.0,
) -> AuctionBar:
    return AuctionBar(
        ts_event_ns=(minute + 1) * NS - 1,
        open=open_,
        high=high,
        low=low,
        close=close,
        base_volume=10.0,
        quote_volume=quote,
        taker_buy_quote_volume=buy_quote,
    )


def base_config(**overrides: object) -> CandidateConfig:
    values = {
        "range_minutes": 60,
        "min_anchor_fraction": 0.9,
        "atr_lookback": 30,
        "flow_lookback": 30,
        "volume_lookback": 30,
        "structure_lookback": 6,
        "min_history": 30,
        "min_excursion_atr": 0.05,
        "max_excursion_atr": 3.0,
        "outside_close_atr": 0.05,
        "reentry_depth_atr": 0.03,
        "attempt_flow_z": 0.3,
        "attempt_volume_z": 0.0,
        "minimum_outside_closes": 2,
        "failure_window_bars": 5,
        "confirmation_bars": 4,
        "min_displacement_atr": 0.3,
        "min_reversal_flow_z": 0.3,
        "max_structure_overshoot_atr": 100.0,
        "stop_buffer_atr": 0.1,
        "minimum_stop_atr": 0.5,
        "min_reward_risk": 1.2,
    }
    values.update(overrides)
    return CandidateConfig(**values)


def completed_anchor() -> list[AuctionBar]:
    result: list[AuctionBar] = []
    for minute in range(60):
        close = 108.0 + ((minute % 5) - 2) * 0.05
        high = 110.0 if minute == 12 else close + 0.20
        low = 100.0 if minute == 25 else close - 0.20
        # Alternate small historical flow so an extreme current imbalance has a
        # finite causal z-score instead of a zero-variance fallback.
        buy_quote = 470.0 if minute % 2 == 0 else 530.0
        result.append(
            bar(
                minute,
                close - 0.03,
                high,
                low,
                close,
                quote=1_000.0,
                buy_quote=buy_quote,
            ),
        )
    return result


class CandidateCoreTest(unittest.TestCase):
    def test_aggressive_flow_is_signed_and_bounded(self) -> None:
        item = bar(0, 100.0, 101.0, 99.0, 100.5, quote=1_000.0, buy_quote=700.0)
        self.assertAlmostEqual(item.signed_aggressive_quote, 400.0)
        self.assertAlmostEqual(item.aggressive_imbalance, 0.4)
        with self.assertRaises(ValueError):
            bar(1, 100.0, 101.0, 99.0, 100.5, quote=1_000.0, buy_quote=1_100.0)

    def test_range_is_not_observable_until_completed(self) -> None:
        machine = AuctionStateMachine(base_config())
        anchor = completed_anchor()
        for item in anchor:
            machine.on_bar(item)
        self.assertIsNone(machine.anchor)
        machine.on_bar(bar(60, 109.5, 109.7, 109.0, 109.4))
        self.assertIsNotNone(machine.anchor)
        self.assertEqual(machine.anchor.high, 110.0)
        self.assertEqual(machine.anchor.low, 100.0)
        events = [event for event in machine.transitions if event.event_type == "DEALING_RANGE_CONFIRMED"]
        self.assertEqual(len(events), 1)
        self.assertGreaterEqual(events[0].observed_time_ns, events[0].event_time_ns)

    def test_sweep_failure_requires_probe_reentry_and_opposite_displacement(self) -> None:
        machine = AuctionStateMachine(base_config(enable_acceptance_failure=False))
        sequence = completed_anchor()
        sequence.extend(
            [
                bar(60, 109.8, 110.8, 109.0, 109.4, quote=2_000.0, buy_quote=1_750.0),
                bar(61, 109.2, 109.3, 105.8, 106.2, quote=3_000.0, buy_quote=300.0),
            ],
        )
        plans = [plan for item in sequence if (plan := machine.on_bar(item)) is not None]
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.side, Side.SHORT)
        self.assertEqual(plan.response, Response.SWEEP_FAILURE)
        self.assertLess(plan.target_price, plan.expected_entry)
        self.assertGreater(plan.stop_price, plan.expected_entry)
        self.assertGreaterEqual(plan.estimated_reward_risk, 1.2)
        event_types = [event.event_type for event in machine.transitions]
        self.assertIn("LIQUIDITY_PROBE_REJECTED", event_types)
        self.assertIn("REVERSAL_DISPLACEMENT_CONFIRMED", event_types)
        self.assertIn("TRADE_PLAN_EMITTED", event_types)
        self.assertLess(
            event_types.index("LIQUIDITY_PROBE_REJECTED"),
            event_types.index("REVERSAL_DISPLACEMENT_CONFIRMED"),
        )

    def test_overextended_displacement_is_not_chased(self) -> None:
        machine = AuctionStateMachine(
            base_config(
                enable_acceptance_failure=False,
                max_structure_overshoot_atr=0.25,
            ),
        )
        sequence = completed_anchor()
        sequence.extend(
            [
                bar(60, 109.8, 110.8, 109.0, 109.4, quote=2_000.0, buy_quote=1_750.0),
                bar(61, 109.2, 109.3, 103.5, 104.0, quote=3_000.0, buy_quote=250.0),
            ],
        )
        plans = [plan for item in sequence if (plan := machine.on_bar(item)) is not None]
        self.assertEqual(plans, [])
        reasons = [event.reason_code for event in machine.transitions]
        self.assertIn("REVERSAL_DISPLACEMENT_ALREADY_OVEREXTENDED", reasons)

    def test_acceptance_failure_requires_two_outside_closes_then_reentry(self) -> None:
        machine = AuctionStateMachine(base_config(enable_sweep_failure=False))
        sequence = completed_anchor()
        sequence.extend(
            [
                bar(60, 109.8, 110.9, 109.7, 110.6, quote=2_200.0, buy_quote=2_000.0),
                bar(61, 110.5, 111.2, 110.3, 110.9, quote=2_000.0, buy_quote=1_800.0),
                bar(62, 110.7, 110.8, 109.2, 109.5, quote=2_500.0, buy_quote=350.0),
                bar(63, 109.4, 109.5, 106.8, 107.0, quote=3_000.0, buy_quote=250.0),
            ],
        )
        plans = [plan for item in sequence if (plan := machine.on_bar(item)) is not None]
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.side, Side.SHORT)
        self.assertEqual(plan.response, Response.ACCEPTANCE_FAILURE)
        event_types = [event.event_type for event in machine.transitions]
        self.assertIn("OUTSIDE_AUCTION_TEST_STARTED", event_types)
        self.assertIn("OUTSIDE_AUCTION_FAILED", event_types)
        self.assertIn("REVERSAL_DISPLACEMENT_CONFIRMED", event_types)

    def test_persistent_outside_value_is_not_faded(self) -> None:
        machine = AuctionStateMachine(
            base_config(enable_sweep_failure=False, failure_window_bars=3),
        )
        sequence = completed_anchor()
        sequence.extend(
            [
                bar(60, 109.8, 110.9, 109.7, 110.6, quote=2_200.0, buy_quote=2_000.0),
                bar(61, 110.5, 111.2, 110.3, 110.9, quote=2_000.0, buy_quote=1_800.0),
                bar(62, 110.8, 111.5, 110.6, 111.2, quote=1_900.0, buy_quote=1_700.0),
                bar(63, 111.1, 111.8, 110.9, 111.5, quote=1_800.0, buy_quote=1_600.0),
                bar(64, 111.4, 112.0, 111.2, 111.7, quote=1_700.0, buy_quote=1_500.0),
            ],
        )
        plans = [plan for item in sequence if (plan := machine.on_bar(item)) is not None]
        self.assertEqual(plans, [])
        reasons = [event.reason_code for event in machine.transitions]
        self.assertIn("OUTSIDE_VALUE_PERSISTED_WITHOUT_FAILURE", reasons)

    def test_future_bars_cannot_change_already_observed_events(self) -> None:
        prefix = completed_anchor() + [
            bar(60, 109.8, 110.8, 109.0, 109.4, quote=2_000.0, buy_quote=1_750.0),
        ]
        first = AuctionStateMachine(base_config(enable_acceptance_failure=False))
        second = AuctionStateMachine(base_config(enable_acceptance_failure=False))
        for item in prefix:
            first.on_bar(item)
            second.on_bar(item)
        observed_first = [event.to_dict() for event in first.transitions]
        observed_second = [event.to_dict() for event in second.transitions]
        self.assertEqual(observed_first, observed_second)

        first.on_bar(bar(61, 109.2, 109.3, 105.8, 106.2, quote=3_000.0, buy_quote=300.0))
        second.on_bar(bar(61, 109.3, 112.0, 109.0, 111.0, quote=3_000.0, buy_quote=2_700.0))
        self.assertEqual(
            observed_first,
            [event.to_dict() for event in first.transitions[: len(observed_first)]],
        )
        self.assertEqual(
            observed_second,
            [event.to_dict() for event in second.transitions[: len(observed_second)]],
        )

    def test_global_gate_counts_pending_entry_as_occupied(self) -> None:
        gate = GlobalEntryGate()
        self.assertTrue(gate.acquire("BTC-scenario"))
        self.assertFalse(gate.acquire("ETH-scenario"))
        self.assertTrue(gate.acquire("BTC-scenario"))
        gate.release("ETH-scenario")
        self.assertEqual(gate.owner, "BTC-scenario")
        gate.release("BTC-scenario")
        self.assertTrue(gate.acquire("SOL-scenario"))


class CandidateProtocolTest(unittest.TestCase):
    def test_seeded_random_week_order_is_reproducible(self) -> None:
        values = seeded_weeks(
            seed=4_012_026,
            start=date(2022, 1, 3),
            end=date(2025, 12, 22),
            count=6,
        )
        self.assertEqual(
            [value.isoformat() for value in values],
            [
                "2023-06-19",
                "2022-08-01",
                "2025-11-10",
                "2025-12-15",
                "2023-02-13",
                "2022-11-21",
            ],
        )


class CandidateDataTest(unittest.TestCase):
    def test_archive_parser_handles_millisecond_timestamps_and_headerless_rows(self) -> None:
        rows = [
            "1704067200000,42000,42100,41900,42050,10,1704067259999,420500,100,6,252300,0",
            "1704067260000,42050,42150,42000,42100,12,1704067319999,505200,120,7,294700,0",
        ]
        payload = BytesIO()
        with ZipFile(payload, "w") as archive:
            archive.writestr("BTCUSDT-1m-2024-01.csv", "\n".join(rows) + "\n")
        frame = _read_archive(payload.getvalue(), "BTCUSDT-1m-2024-01.csv")
        self.assertEqual(len(frame), 2)
        self.assertTrue(str(frame.loc[0, "close_dt"]).startswith("2024-01-01 00:00:59"))
        bars = to_auction_bars(frame)
        self.assertEqual(len(bars), 2)
        self.assertAlmostEqual(bars[0].aggressive_imbalance, 0.2)


if __name__ == "__main__":
    unittest.main()
