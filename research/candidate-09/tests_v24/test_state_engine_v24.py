from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from pathlib import Path

import state_engine_v24_direct as v24

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config_v24.json").read_text(encoding="utf-8"))
MINUTE = v24.MINUTE_NS


def bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    index_ohlc: tuple[float, float, float, float] | None = None,
    volume: float = 100.0,
    flow: float = 0.0,
    metric_observed_ns: int | None = None,
    oi: float | None = None,
) -> v24.FlowBar:
    index_ohlc = index_ohlc or (open_, high, low, close)
    return v24.FlowBar(
        ts_ns=(index + 1) * MINUTE,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=volume * (flow + 1.0) / 2.0,
        trade_count=100,
        index_open=index_ohlc[0],
        index_high=index_ohlc[1],
        index_low=index_ohlc[2],
        index_close=index_ohlc[3],
        metric_observed_ns=metric_observed_ns,
        open_interest=oi,
        open_interest_value=(oi * close if oi is not None else None),
        metric_taker_ratio=None,
        top_trader_account_ratio=(1.1 if oi is not None else None),
        top_trader_position_ratio=(1.2 if oi is not None else None),
        global_account_ratio=(1.0 if oi is not None else None),
    )


def warmed_down_dislocation(ablation: str = "baseline") -> v24.LiquidityStateEngine:
    engine = v24.LiquidityStateEngine(v24.EngineConfig.from_mapping(CONFIG, ablation=ablation))
    engine._metric_changes.extend([0.0002, -0.0002] * 12)
    engine._last_open_interest = 100_000.0
    engine._last_metric_observed_ns = 200 * MINUTE

    # 240 completed bars establish a stable near-zero futures/index basis and gap.
    for i in range(240):
        close = 100.02 if i % 2 else 99.98
        engine.on_bar(bar(
            i,
            open_=100.0,
            high=100.1,
            low=99.9,
            close=close,
            index_ohlc=(100.0, 100.1, 99.9, close),
        ))

    # Frozen source auction immediately before the five-minute positioning pulse.
    for i in range(240, 255):
        engine.on_bar(bar(
            i,
            open_=100.0,
            high=101.0 if i == 245 else 100.3,
            low=99.0 if i == 246 else 99.7,
            close=100.0,
            index_ohlc=(100.0, 100.2, 99.8, 100.0),
        ))

    futures = [
        (100.0, 100.1, 98.8, 99.0),
        (99.0, 99.1, 97.8, 98.0),
        (98.0, 98.1, 96.8, 97.0),
        (97.0, 97.1, 95.9, 96.0),
        (96.0, 96.1, 95.4, 95.5),
    ]
    index = [
        (100.0, 100.02, 99.85, 99.9),
        (99.9, 99.95, 99.75, 99.8),
        (99.8, 99.85, 99.65, 99.7),
        (99.7, 99.75, 99.55, 99.6),
        (99.6, 99.65, 99.45, 99.5),
    ]
    for i, (future_ohlc, index_ohlc) in enumerate(zip(futures, index, strict=True), start=255):
        engine.on_bar(bar(
            i,
            open_=future_ohlc[0],
            high=future_ohlc[1],
            low=future_ohlc[2],
            close=future_ohlc[3],
            index_ohlc=index_ohlc,
            volume=220.0,
            flow=-0.35,
        ))
    return engine


def observe_down_dislocation(
    engine: v24.LiquidityStateEngine,
    *,
    oi: float = 99_500.0,
    index_close: float = 99.5,
) -> v24.EngineResult:
    return engine.on_bar(bar(
        260,
        open_=95.5,
        high=95.7,
        low=95.35,
        close=95.5,
        index_ohlc=(index_close, index_close + 0.05, index_close - 0.05, index_close),
        volume=120.0,
        flow=-0.10,
        metric_observed_ns=261 * MINUTE,
        oi=oi,
    ))


class V24IndexAnchoredLiquidationContractTest(unittest.TestCase):
    def test_ablation_mapping_changes_only_declared_causal_layers(self):
        baseline = v24.EngineConfig.from_mapping(CONFIG, ablation="baseline")
        no_oi = v24.EngineConfig.from_mapping(CONFIG, ablation="no-oi")
        no_gap = v24.EngineConfig.from_mapping(CONFIG, ablation="no-index-gap")
        no_reclaim = v24.EngineConfig.from_mapping(CONFIG, ablation="no-reclaim")
        self.assertFalse(no_oi.require_oi)
        self.assertFalse(no_gap.require_index_gap)
        self.assertFalse(no_reclaim.require_reclaim)
        common = set(asdict(baseline)) - {"require_oi", "require_index_gap", "require_reclaim"}
        for name in common:
            self.assertEqual(getattr(baseline, name), getattr(no_oi, name), name)
            self.assertEqual(getattr(baseline, name), getattr(no_gap, name), name)
            self.assertEqual(getattr(baseline, name), getattr(no_reclaim, name), name)

    def test_completed_oi_drop_and_futures_index_gap_create_pending_dislocation(self):
        engine = warmed_down_dislocation()
        result = observe_down_dislocation(engine)
        self.assertTrue(any(
            event.event_type == "INDEX_LIQUIDATION_DISLOCATION_CONFIRMED"
            and event.reason_code == "ABNORMAL_OI_DROP_WITH_FUTURES_INDEX_DISLOCATION"
            for event in result.events
        ))
        self.assertIsNotNone(engine._pending)
        assert engine._pending is not None
        self.assertEqual(engine._pending.direction, "DOWN")
        self.assertLess(engine._pending.initial_basis_dislocation, 0.0)
        self.assertLess(engine._pending.pulse_return_gap, 0.0)
        self.assertLess(engine._pending.oi_change_fraction, 0.0)

    def test_basis_reclaim_internal_shift_builds_cost_after_buy(self):
        engine = warmed_down_dislocation()
        observe_down_dislocation(engine)
        result = engine.on_bar(bar(
            261,
            open_=95.6,
            high=97.0,
            low=95.5,
            close=96.8,
            index_ohlc=(99.5, 99.65, 99.45, 99.6),
            volume=180.0,
            flow=0.30,
        ))
        self.assertIsNotNone(result.signal)
        assert result.signal is not None
        self.assertEqual(result.signal.side, "BUY")
        self.assertEqual(result.signal.branch, "REVERSAL")
        self.assertLess(result.signal.stop_price, result.signal.entry_reference)
        self.assertGreater(result.signal.target_price, result.signal.entry_reference)
        self.assertGreaterEqual(result.signal.net_reward_to_risk, 1.2)
        self.assertEqual(
            result.signal.details["confirmation"],
            "basis-reclaim-plus-internal-structure-shift",
        )

    def test_oi_and_index_gap_controls_are_independent(self):
        baseline = warmed_down_dislocation("baseline")
        observe_down_dislocation(baseline, oi=100_001.0)
        self.assertIsNone(baseline._pending)

        no_oi = warmed_down_dislocation("no-oi")
        observe_down_dislocation(no_oi, oi=100_001.0)
        self.assertIsNotNone(no_oi._pending)

        no_gap = warmed_down_dislocation("no-index-gap")
        # With index matching the futures close there is no derivatives-specific gap,
        # but the exact control admits the otherwise identical OI pulse.
        observe_down_dislocation(no_gap, index_close=95.5)
        self.assertIsNotNone(no_gap._pending)

    def test_no_reclaim_control_enters_on_metric_availability_close(self):
        engine = warmed_down_dislocation("no-reclaim")
        result = observe_down_dislocation(engine)
        self.assertIsNotNone(result.signal)
        assert result.signal is not None
        self.assertEqual(result.signal.details["confirmation"], "pulse-close-control")

    def test_target_first_and_timeout_expire_without_chasing(self):
        target_first = warmed_down_dislocation()
        observe_down_dislocation(target_first)
        result = target_first.on_bar(bar(
            261,
            open_=95.6,
            high=100.0,
            low=95.5,
            close=99.7,
            index_ohlc=(99.5, 99.7, 99.4, 99.6),
            flow=0.30,
        ))
        self.assertIsNone(result.signal)
        self.assertTrue(any(
            event.reason_code == "FAIR_BASIS_TARGET_REACHED_BEFORE_ENTRY"
            for event in result.events
        ))

        timeout = warmed_down_dislocation()
        observe_down_dislocation(timeout)
        last = None
        for i in range(261, 265):
            last = timeout.on_bar(bar(
                i,
                open_=95.5,
                high=95.8,
                low=95.3,
                close=95.6,
                index_ohlc=(99.5, 99.6, 99.4, 99.5),
                flow=-0.05,
            ))
        assert last is not None
        self.assertIsNone(last.signal)
        self.assertTrue(any(
            event.reason_code == "INDEX_DISLOCATION_DID_NOT_RECLAIM_IN_TIME"
            for event in last.events
        ))


if __name__ == "__main__":
    unittest.main()
