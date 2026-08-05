from __future__ import annotations

from dataclasses import replace
from datetime import date
import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lcpt_data import normalize_timestamp_ns
from lcpt_engine import (
    LcptReplay,
    expected_loss_budget_per_unit,
    lock_trigger_price,
    target_trigger_price,
)
from lcpt_features import detect_cascade_signals
from lcpt_model import (
    CascadeSignal,
    FiveMinuteBar,
    FiveMinuteState,
    LcptConfig,
    MinuteBar,
    NS_PER_MINUTE,
)
from select_lcpt_weeks import EXPECTED, select_validation_weeks


def minute_bar(minute: int, price: float = 100.0) -> MinuteBar:
    start = minute * NS_PER_MINUTE
    return MinuteBar(
        minute_start_ns=start,
        open=price,
        high=price + 0.5,
        low=price - 0.5,
        close=price,
        volume=1.0,
        notional=price,
        signed_notional=0.0,
        trade_count=1,
        first_trade_id=minute,
        last_trade_id=minute,
        first_event_time_ns=start + 1,
        last_event_time_ns=start + 1,
    )


def five(boundary_minute: int, close: float, flow: float) -> FiveMinuteBar:
    notional = 1000.0
    return FiveMinuteBar(
        boundary_ns=boundary_minute * NS_PER_MINUTE,
        open=close,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=10.0,
        notional=notional,
        signed_notional=flow * notional,
        trade_count=10,
    )


def write_agg_zip(path: Path, rows: list[tuple[int, float, int]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for aggregate_id, price, time_ns in rows:
        writer.writerow(
            [
                aggregate_id,
                f"{price:.8f}",
                "1.00000000",
                aggregate_id,
                aggregate_id,
                time_ns // 1_000_000,
                "false",
            ],
        )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(path.with_suffix(".csv").name, buffer.getvalue())


class ConfigAndSelectionTests(unittest.TestCase):
    def test_frozen_validation_selection(self) -> None:
        self.assertEqual(select_validation_weeks(), EXPECTED)

    def test_risk_fraction_cannot_exceed_three_percent(self) -> None:
        with self.assertRaises(ValueError):
            replace(LcptConfig(), risk_fraction=0.031).validate()

    def test_timestamp_normalization(self) -> None:
        self.assertEqual(
            normalize_timestamp_ns(1_646_611_200_123),
            1_646_611_200_123_000_000,
        )
        self.assertEqual(
            normalize_timestamp_ns(1_646_611_200_123_456),
            1_646_611_200_123_456_000,
        )


class CausalFeatureTests(unittest.TestCase):
    def test_extension_filter_ends_at_ignition_close(self) -> None:
        config = replace(
            LcptConfig(),
            ignition_price_shock_bps=10.0,
            ignition_oi_drop_bps=1.0,
            continuation_oi_drop_bps=20.0,
            extension_through_ignition_max_bps=50.0,
        )
        minutes = {index * NS_PER_MINUTE: minute_bar(index) for index in range(70)}
        states: list[FiveMinuteState] = []
        closes = [100.0] * 12 + [100.20, 102.0]
        open_interests = [1000.0] * 12 + [999.8, 997.5]
        for index, (close, oi) in enumerate(zip(closes, open_interests), start=1):
            boundary = index * 5
            flow = 0.5 if index >= 13 else 0.0
            states.append(
                FiveMinuteState(
                    boundary_ns=boundary * NS_PER_MINUTE,
                    futures=five(boundary, close, flow),
                    spot=five(boundary, close, flow),
                    open_interest=oi,
                    futures_return_bps=(
                        0.0
                        if index == 1
                        else (close / closes[index - 2] - 1.0) * 10_000.0
                    ),
                    open_interest_change_bps=(
                        0.0
                        if index == 1
                        else (oi / open_interests[index - 2] - 1.0) * 10_000.0
                    ),
                ),
            )
        signals = detect_cascade_signals(
            config,
            minutes,
            states,
            evaluation_start_ns=70 * NS_PER_MINUTE,
            evaluation_end_ns=71 * NS_PER_MINUTE,
        )
        self.assertEqual(len(signals), 1)
        # The 2% continuation bar is deliberately large. It must not enter the
        # 60-minute age filter, which ends at the 0.2% ignition close.
        self.assertAlmostEqual(
            signals[0].extension_through_ignition_bps,
            20.0,
            places=6,
        )


class AccountingTests(unittest.TestCase):
    def test_loss_budget_and_cost_after_target(self) -> None:
        config = LcptConfig()
        entry_raw = 100.0
        stop = 98.0
        direction = 1
        entry_fill, stop_fill, max_funding, loss = expected_loss_budget_per_unit(
            config,
            entry_raw,
            stop,
            direction,
        )
        planned = config.initial_nav * config.risk_fraction
        quantity = planned / loss
        self.assertAlmostEqual(quantity * loss, planned)

        target = target_trigger_price(
            config,
            entry_fill,
            loss,
            direction,
            max_funding,
        )
        fee = config.taker_fee_bps / 10_000.0
        slip = config.slippage_impact_bps / 10_000.0
        exit_fill = target * (1.0 - slip)
        net_per_unit = (
            exit_fill
            - entry_fill
            - entry_fill * fee
            - exit_fill * fee
            - max_funding
        )
        self.assertGreaterEqual(
            net_per_unit,
            config.target_net_r * loss - 1e-10,
        )

    def test_lock_trigger_is_net_of_current_costs(self) -> None:
        config = LcptConfig()
        entry_fill, _, _, loss = expected_loss_budget_per_unit(
            config,
            100.0,
            98.0,
            1,
        )
        accrued = 0.01
        raw = lock_trigger_price(config, entry_fill, loss, 1, accrued)
        fee = config.taker_fee_bps / 10_000.0
        slip = config.slippage_impact_bps / 10_000.0
        exit_fill = raw * (1.0 - slip)
        net = (
            exit_fill
            - entry_fill
            - entry_fill * fee
            - exit_fill * fee
            - accrued
        )
        self.assertAlmostEqual(
            net,
            config.protection_lock_net_r * loss,
            places=9,
        )


class StateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = replace(
            LcptConfig(),
            entry_buffer_minutes=1,
            max_holding_minutes=10,
        )
        self.minutes = {
            index * NS_PER_MINUTE: minute_bar(index)
            for index in range(120)
        }

    def signal(self, scenario: str = "synthetic") -> CascadeSignal:
        return CascadeSignal(
            scenario_id=scenario,
            direction=1,
            ignition_time_ns=5 * NS_PER_MINUTE,
            confirmation_time_ns=10 * NS_PER_MINUTE,
            cascade_high=101.0,
            cascade_low=99.2,
            atr=1.0,
            stop_trigger_price=99.0,
            ignition_return_bps=20.0,
            ignition_oi_drop_bps=2.0,
            continuation_return_bps=10.0,
            continuation_oi_drop_bps=25.0,
            ignition_futures_flow=0.4,
            ignition_spot_flow=0.2,
            continuation_futures_flow=0.3,
            continuation_spot_flow=0.1,
            extension_through_ignition_bps=30.0,
        )

    def test_buffer_invalidation_prevents_entry(self) -> None:
        events: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trades.zip"
            write_agg_zip(
                path,
                [
                    (1, 100.0, 10 * NS_PER_MINUTE + 1),
                    (2, 98.5, 10 * NS_PER_MINUTE + 30_000_000_000),
                    (3, 100.0, 11 * NS_PER_MINUTE + 1),
                ],
            )
            result = LcptReplay(
                self.config,
                self.minutes,
                [self.signal()],
                lambda **values: events.append(values),
                10 * NS_PER_MINUTE,
                12 * NS_PER_MINUTE,
            ).run([str(path)])
        self.assertEqual(result["invalidated_before_entry"], 1)
        self.assertEqual(result["trades_detail"], [])
        self.assertTrue(
            any(event["event_type"] == "ENTRY_BUFFER_INVALIDATED" for event in events),
        )

    def test_first_trade_after_valid_buffer_is_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trades.zip"
            write_agg_zip(
                path,
                [
                    (1, 100.0, 10 * NS_PER_MINUTE + 1),
                    (2, 100.2, 10 * NS_PER_MINUTE + 59_000_000_000),
                    (3, 100.5, 11 * NS_PER_MINUTE + 1),
                    (4, 100.6, 11 * NS_PER_MINUTE + 30_000_000_000),
                ],
            )
            result = LcptReplay(
                self.config,
                self.minutes,
                [self.signal()],
                lambda **values: None,
                10 * NS_PER_MINUTE,
                12 * NS_PER_MINUTE,
            ).run([str(path)])
        self.assertEqual(len(result["trades_detail"]), 1)
        self.assertEqual(result["trades_detail"][0]["entry_trade_id"], 3)
        self.assertTrue(result["single_slot_enforced"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
