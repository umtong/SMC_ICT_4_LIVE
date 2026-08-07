from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import os
import unittest
from unittest.mock import patch

import pandas as pd

from c10_v33_overlay import normalize_kline_open_time
from c10_v33_overlay import reframe_primary_equilibrium


class Scenario(StrEnum):
    FAR = "FAR"
    AAC = "AAC"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class Plan:
    scenario_id: str
    scenario: Scenario
    direction: Direction
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    atr: float
    loss_per_unit: float
    gain_per_unit: float
    net_r: float
    reason_code: str
    expire_ts_ns: int
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class Pool:
    scenario_id: str
    level: float
    opposite_level: float | None


@dataclass
class Bar:
    ts_ns: int
    high: float
    low: float


@dataclass
class Config:
    stop_buffer_atr: float = 0.08
    effective_maker_rate: float = 0.0004
    effective_taker_rate: float = 0.0008
    min_net_r: float = 1.25


@dataclass
class Logic:
    pools: list[Pool]
    bars: list[Bar]
    config: Config = field(default_factory=Config)


def long_plan() -> Plan:
    return Plan(
        scenario_id="LOW-RAID",
        scenario=Scenario.FAR,
        direction=Direction.LONG,
        observed_ts_ns=300,
        expected_entry=96.0,
        stop_price=88.0,
        target_price=112.0,
        atr=10.0,
        loss_per_unit=8.1088,
        gain_per_unit=15.9168,
        net_r=1.96,
        reason_code="BASE",
        expire_ts_ns=400,
        details={
            "sweep_ts_ns": 100,
            "zone_low": 94.0,
            "zone_high": 96.0,
        },
    )


def logic(*, delivered: bool = False, opposite: float | None = 110.0) -> Logic:
    return Logic(
        pools=[Pool("LOW-RAID", 90.0, opposite)],
        bars=[
            Bar(100, 92.0, 89.0),
            Bar(200, 99.0, 93.0),
            Bar(300, 100.1 if delivered else 99.5, 95.0),
        ],
    )


class PrimaryEquilibriumContractTest(unittest.TestCase):
    def test_mixed_archive_timestamps_are_normalized_to_int64(self) -> None:
        frame = pd.DataFrame(
            {
                "open_time": ["1641254400000", 1641254460000],
                "close": ["1.0", "1.1"],
            },
        )
        normalized = normalize_kline_open_time(
            frame,
            "BTCUSDT-1m-2022-01-04.zip",
        )
        self.assertEqual(str(normalized["open_time"].dtype), "int64")
        self.assertEqual(
            normalized["open_time"].tolist(),
            [1641254400000, 1641254460000],
        )

    def test_exact_baseline_is_unchanged(self) -> None:
        plan = long_plan()
        with patch.dict(
            os.environ,
            {
                "C10_V33_EQUILIBRIUM_TARGET": "0",
                "C10_V33_ZONE_INVALIDATION": "0",
            },
        ):
            result = reframe_primary_equilibrium(plan, logic())
        self.assertTrue(result.approved)
        self.assertIs(result.plan, plan)
        self.assertEqual(result.reason, "EXACT_BASELINE_UNCHANGED")

    def test_equilibrium_target_is_source_midpoint(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V33_EQUILIBRIUM_TARGET": "1",
                "C10_V33_ZONE_INVALIDATION": "1",
            },
        ):
            result = reframe_primary_equilibrium(long_plan(), logic())
        self.assertTrue(result.approved)
        self.assertAlmostEqual(result.plan.target_price, 100.0)
        self.assertAlmostEqual(result.plan.stop_price, 93.2)
        details = result.plan.details["primary_equilibrium"]
        self.assertEqual(details["original_independent_external_draw"], 112.0)
        self.assertEqual(
            details["runner_contract"].split(";")[0],
            "NOT_PART_OF_PRIMARY_TRADE",
        )

    def test_equilibrium_delivered_before_plan_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V33_EQUILIBRIUM_TARGET": "1",
                "C10_V33_ZONE_INVALIDATION": "1",
            },
        ):
            result = reframe_primary_equilibrium(long_plan(), logic(delivered=True))
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SOURCE_EQUILIBRIUM_DELIVERED_BEFORE_ENTRY_PLAN",
        )

    def test_missing_source_pair_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V33_EQUILIBRIUM_TARGET": "1",
                "C10_V33_ZONE_INVALIDATION": "1",
            },
        ):
            result = reframe_primary_equilibrium(
                long_plan(),
                logic(opposite=None),
            )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SOURCE_RANGE_ENDPOINTS_UNAVAILABLE")

    def test_target_and_stop_are_independent_ablation_axes(self) -> None:
        plan = long_plan()
        with patch.dict(
            os.environ,
            {
                "C10_V33_EQUILIBRIUM_TARGET": "0",
                "C10_V33_ZONE_INVALIDATION": "1",
            },
        ):
            stop_only = reframe_primary_equilibrium(plan, logic())
        with patch.dict(
            os.environ,
            {
                "C10_V33_EQUILIBRIUM_TARGET": "1",
                "C10_V33_ZONE_INVALIDATION": "0",
            },
        ):
            target_only = reframe_primary_equilibrium(plan, logic())
        self.assertTrue(stop_only.approved)
        self.assertEqual(stop_only.plan.target_price, 112.0)
        self.assertAlmostEqual(stop_only.plan.stop_price, 93.2)
        self.assertFalse(target_only.approved)
        self.assertEqual(
            target_only.reason,
            "PRIMARY_EQUILIBRIUM_INSUFFICIENT_COSTED_STRUCTURAL_R",
        )

    def test_non_far_is_not_reframed(self) -> None:
        plan = long_plan()
        aac = Plan(
            **{
                **plan.__dict__,
                "scenario": Scenario.AAC,
            },
        )
        with patch.dict(
            os.environ,
            {
                "C10_V33_EQUILIBRIUM_TARGET": "1",
                "C10_V33_ZONE_INVALIDATION": "1",
            },
        ):
            result = reframe_primary_equilibrium(aac, logic())
        self.assertTrue(result.approved)
        self.assertIs(result.plan, aac)
        self.assertEqual(result.reason, "NON_FAR_UNCHANGED")


if __name__ == "__main__":
    unittest.main()
