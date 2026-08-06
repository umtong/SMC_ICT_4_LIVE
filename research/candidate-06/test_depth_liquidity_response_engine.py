from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from depth_liquidity_response_engine import (
    DepthLiquidityVacuumReplenishmentEngine,
    PriorOnlyPassiveLiquidityDetector,
    PassiveLiquidityRecord,
)
from lrb_types import BarObservation, PrimitiveSnapshot
from prepare_liquidity_series import _parse_timestamp_ns


MINUTE = 60_000_000_000


def snap(
    index: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    flow: float,
    *,
    timestamp: int | None = None,
) -> PrimitiveSnapshot:
    volume = 1_000.0
    buy = volume * (flow + 1.0) / 2.0
    width = max(high - low, 0.1)
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(
            ts_ns=(index + 1) * MINUTE if timestamp is None else timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            taker_buy_volume=buy,
            trades=100,
        ),
        ready=True,
        atr=1.0,
        rel_volume=1.0,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=max(high - max(open_, close), 0.0) / width,
        lower_wick_fraction=max(min(open_, close) - low, 0.0) / width,
        close_location=(close - low) / width,
        upper_fast=100.0,
        lower_fast=90.0,
        upper_slow=105.0,
        lower_slow=85.0,
        slow_mid=95.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


class DetectorTests(unittest.TestCase):
    def test_current_record_is_not_in_its_own_baseline(self) -> None:
        detector = PriorOnlyPassiveLiquidityDetector(warmup=4)
        for index in range(4):
            record = PassiveLiquidityRecord(index + 1, 100.0, 100.0, 400.0, 400.0)
            features = detector.features(record)
            self.assertEqual(features.prior_observations, index)
            detector.commit(record)
        shock = PassiveLiquidityRecord(5, 180.0, 20.0, 600.0, 200.0)
        features = detector.features(shock)
        self.assertGreater(features.bid_near_z, 1_000.0)
        self.assertLess(features.ask_near_z, -1_000.0)
        self.assertEqual(features.prior_observations, 4)


class DepthScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "passive-liquidity.csv"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_records(self, shock: tuple[float, float] = (180.0, 20.0)) -> None:
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["ts_ns", "bid_near", "ask_near", "bid_total", "ask_total"],
            )
            writer.writeheader()
            for minute in range(1, 25):
                writer.writerow(
                    {
                        "ts_ns": minute * MINUTE,
                        "bid_near": 120.0,
                        "ask_near": 120.0,
                        "bid_total": 500.0,
                        "ask_total": 500.0,
                    },
                )
            writer.writerow(
                {
                    "ts_ns": 25 * MINUTE,
                    "bid_near": shock[0],
                    "ask_near": shock[1],
                    "bid_total": 700.0,
                    "ask_total": 250.0,
                },
            )

    def params(self, **overrides: object) -> dict[str, object]:
        params: dict[str, object] = {
            "depth_series_path": str(self.path),
            "dlvr_liquidity_source": "bookTicker",
            "depth_max_age_minutes": 10,
            "dlvr_depth_warmup": 24,
            "dlvr_flow_ratio": 0.10,
            "dlvr_body_atr": 0.30,
            "dlvr_vacuum_z": -0.50,
            "dlvr_support_z": 0.0,
            "dlvr_near_imbalance": 0.05,
            "dlvr_replenish_z": 0.75,
            "dlvr_replenish_change": 0.20,
            "dlvr_reversal_imbalance": 0.03,
            "dlvr_require_depth_confirmation": True,
            "dlvr_enable_vacuum": True,
            "dlvr_enable_replenishment_reversal": True,
            "dlvr_retest_bars": 12,
            "dlvr_boundary_tolerance_atr": 0.08,
            "dlvr_retest_band_atr": 0.30,
            "dlvr_retest_max_flow": 0.12,
            "dlvr_response_flow": 0.10,
            "dlvr_response_body_atr": 0.20,
            "dlvr_response_imbalance": 0.0,
            "dlvr_stop_buffer_atr": 0.08,
            "minimum_structural_rr": 0.50,
            "dlvr_projection_fraction": 2.0,
            "cooldown_bars": 2,
        }
        params.update(overrides)
        return params

    def seed(self, engine: DepthLiquidityVacuumReplenishmentEngine) -> None:
        for index in range(24):
            step = engine.observe(snap(index, 95.0, 95.2, 94.8, 95.0, 0.0), allow_new=True)
            self.assertFalse(step.transitions)
            self.assertIsNone(step.signal)

    def test_vacuum_requires_later_retest_and_separate_response(self) -> None:
        self.write_records()
        engine = DepthLiquidityVacuumReplenishmentEngine(self.params())
        self.seed(engine)

        started = engine.observe(
            snap(24, 99.8, 101.4, 99.7, 101.2, 0.50),
            allow_new=True,
        )
        self.assertEqual(started.transitions[-1].next_state, "PROVISIONAL_PASSIVE_LIQUIDITY_SHOCK")
        self.assertIsNone(started.signal)

        retest = engine.observe(
            snap(25, 101.2, 101.3, 100.1, 100.5, -0.20),
            allow_new=True,
        )
        self.assertEqual(retest.transitions[-1].next_state, "STRUCTURAL_RETEST_OBSERVED")
        self.assertIsNone(retest.signal)

        response = engine.observe(
            snap(26, 100.5, 101.7, 100.4, 101.6, 0.40),
            allow_new=True,
        )
        self.assertIsNotNone(response.signal)
        assert response.signal is not None
        self.assertEqual(response.signal.family, "DLVC")
        self.assertTrue(response.signal.details["depth_confirmation_required"])

    def test_stale_passive_liquidity_cannot_start_repeated_scenarios(self) -> None:
        self.write_records()
        engine = DepthLiquidityVacuumReplenishmentEngine(self.params())
        self.seed(engine)
        started = engine.observe(snap(24, 99.8, 101.4, 99.7, 101.2, 0.50), allow_new=True)
        self.assertTrue(started.transitions)
        engine.abort_active(snap(24, 101.2, 101.3, 100.8, 101.0, 0.0), "TEST_ABORT")
        repeated = engine.observe(snap(25, 99.8, 101.4, 99.7, 101.2, 0.50), allow_new=True)
        self.assertFalse(repeated.transitions)
        self.assertIsNone(repeated.signal)

    def test_data_older_than_contract_resets_active_episode(self) -> None:
        self.write_records()
        engine = DepthLiquidityVacuumReplenishmentEngine(
            self.params(depth_max_age_minutes=1),
        )
        self.seed(engine)
        engine.observe(snap(24, 99.8, 101.4, 99.7, 101.2, 0.50), allow_new=True)
        stale = engine.observe(
            snap(27, 101.0, 101.2, 100.6, 100.9, 0.0),
            allow_new=True,
        )
        self.assertEqual(stale.transitions[-1].reason_code, "PASSIVE_LIQUIDITY_DATA_STALE")

    def test_price_only_ablation_removes_only_passive_liquidity_gate(self) -> None:
        self.write_records(shock=(120.0, 120.0))
        full = DepthLiquidityVacuumReplenishmentEngine(self.params())
        ablation = DepthLiquidityVacuumReplenishmentEngine(
            self.params(dlvr_require_depth_confirmation=False),
        )
        self.seed(full)
        self.seed(ablation)
        event = snap(24, 99.8, 101.4, 99.7, 101.2, 0.50)
        self.assertFalse(full.observe(event, allow_new=True).transitions)
        ablated = ablation.observe(event, allow_new=True)
        self.assertTrue(ablated.transitions)
        self.assertFalse(ablated.transitions[-1].details["depth_confirmation_required"])


class PassiveLiquidityTimestampTests(unittest.TestCase):
    def test_numeric_seconds_timestamp_is_preserved(self):
        self.assertEqual(
            _parse_timestamp_ns("1708905600"),
            1_708_905_600_000_000_000,
        )

    def test_iso_timestamp_is_parsed_as_utc(self):
        self.assertEqual(
            _parse_timestamp_ns("2024-02-26 00:00:00"),
            1_708_905_600_000_000_000,
        )


if __name__ == "__main__":
    unittest.main()
