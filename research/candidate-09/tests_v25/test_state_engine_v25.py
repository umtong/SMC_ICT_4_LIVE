from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from pathlib import Path

import state_engine_v25_direct as v25

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config_v25.json").read_text(encoding="utf-8"))
MINUTE = v25.MINUTE_NS


def bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    spot_ohlc: tuple[float, float, float, float] | None = None,
    volume: float = 100.0,
    flow: float = 0.0,
    metric_observed_ns: int | None = None,
    oi: float | None = None,
) -> v25.FlowBar:
    spot_ohlc = spot_ohlc or (open_, high, low, close)
    return v25.FlowBar(
        ts_ns=(index + 1) * MINUTE,
        open=open_, high=high, low=low, close=close,
        volume=volume,
        taker_buy_volume=volume * (flow + 1.0) / 2.0,
        trade_count=100,
        spot_open=spot_ohlc[0], spot_high=spot_ohlc[1],
        spot_low=spot_ohlc[2], spot_close=spot_ohlc[3],
        metric_observed_ns=metric_observed_ns,
        open_interest=oi,
        open_interest_value=(oi * close if oi is not None else None),
        metric_taker_ratio=None,
        top_trader_account_ratio=(1.1 if oi is not None else None),
        top_trader_position_ratio=(1.2 if oi is not None else None),
        global_account_ratio=(1.0 if oi is not None else None),
    )


def warmed_dislocation(direction: str, ablation: str = "baseline", *, spot_follows: bool = False):
    engine = v25.LiquidityStateEngine(v25.EngineConfig.from_mapping(CONFIG, ablation=ablation))
    engine._metric_changes.extend([0.0002, -0.0002] * 12)
    engine._last_open_interest = 100_000.0
    engine._last_metric_observed_ns = 200 * MINUTE

    for i in range(240):
        close = 100.02 if i % 2 else 99.98
        engine.on_bar(bar(i, open_=100.0, high=100.1, low=99.9, close=close,
                          spot_ohlc=(100.0, 100.1, 99.9, close)))
    for i in range(240, 255):
        engine.on_bar(bar(
            i, open_=100.0,
            high=101.0 if i == 245 else 100.3,
            low=99.0 if i == 246 else 99.7,
            close=100.0,
            spot_ohlc=(100.0, 100.2, 99.8, 100.0),
        ))

    if direction == "DOWN":
        futures = [
            (100.0, 100.1, 98.8, 99.0), (99.0, 99.1, 97.8, 98.0),
            (98.0, 98.1, 96.8, 97.0), (97.0, 97.1, 95.9, 96.0),
            (96.0, 96.1, 95.4, 95.5),
        ]
        spots = [
            (100.0, 100.02, 99.85, 99.9), (99.9, 99.95, 99.75, 99.8),
            (99.8, 99.85, 99.65, 99.7), (99.7, 99.75, 99.55, 99.6),
            (99.6, 99.65, 99.45, 99.5),
        ]
        flow = -0.35
    else:
        futures = [
            (100.0, 101.2, 99.9, 101.0), (101.0, 102.2, 100.9, 102.0),
            (102.0, 103.2, 101.9, 103.0), (103.0, 104.2, 102.9, 104.0),
            (104.0, 104.7, 103.9, 104.5),
        ]
        spots = [
            (100.0, 100.15, 99.98, 100.1), (100.1, 100.25, 100.05, 100.2),
            (100.2, 100.35, 100.15, 100.3), (100.3, 100.45, 100.25, 100.4),
            (100.4, 100.55, 100.35, 100.5),
        ]
        flow = 0.35
    if spot_follows:
        spots = futures
    for i, (future_ohlc, spot_ohlc) in enumerate(zip(futures, spots, strict=True), start=255):
        engine.on_bar(bar(
            i, open_=future_ohlc[0], high=future_ohlc[1], low=future_ohlc[2],
            close=future_ohlc[3], spot_ohlc=spot_ohlc,
            volume=220.0, flow=flow,
        ))
    return engine


def observe_metric(engine, direction: str, *, oi: float = 99_500.0, spot_close: float | None = None):
    if direction == "DOWN":
        future = (95.5, 95.7, 95.35, 95.5)
        spot_close = 99.5 if spot_close is None else spot_close
        spot = (spot_close, spot_close + 0.05, spot_close - 0.05, spot_close)
        flow = -0.10
    else:
        future = (104.5, 104.65, 104.3, 104.5)
        spot_close = 100.5 if spot_close is None else spot_close
        spot = (spot_close, spot_close + 0.05, spot_close - 0.05, spot_close)
        flow = 0.10
    return engine.on_bar(bar(
        260, open_=future[0], high=future[1], low=future[2], close=future[3],
        spot_ohlc=spot, volume=120.0, flow=flow,
        metric_observed_ns=261 * MINUTE, oi=oi,
    ))


class V25SpotLedAuctionReversionContractTest(unittest.TestCase):
    def test_ablation_mapping_changes_only_declared_layers(self):
        baseline = v25.EngineConfig.from_mapping(CONFIG, ablation="baseline")
        variants = {
            "no-oi": v25.EngineConfig.from_mapping(CONFIG, ablation="no-oi"),
            "no-spot-gap": v25.EngineConfig.from_mapping(CONFIG, ablation="no-spot-gap"),
            "no-spot-lead": v25.EngineConfig.from_mapping(CONFIG, ablation="no-spot-lead"),
        }
        self.assertFalse(variants["no-oi"].require_oi)
        self.assertFalse(variants["no-spot-gap"].require_spot_gap)
        self.assertFalse(variants["no-spot-lead"].require_spot_lead)
        common = set(asdict(baseline)) - {"require_oi", "require_spot_gap", "require_spot_lead"}
        for variant in variants.values():
            for name in common:
                self.assertEqual(getattr(baseline, name), getattr(variant, name), name)

    def test_completed_oi_drop_and_perpetual_spot_gap_create_pending_state(self):
        engine = warmed_dislocation("DOWN")
        result = observe_metric(engine, "DOWN")
        self.assertTrue(any(
            event.event_type == "SPOT_LED_LIQUIDATION_DISLOCATION_CONFIRMED"
            and event.reason_code == "ABNORMAL_OI_DROP_WITH_PERPETUAL_SPOT_DISLOCATION"
            for event in result.events
        ))
        self.assertIsNotNone(engine._pending)
        assert engine._pending is not None
        self.assertEqual(engine._pending.direction, "DOWN")
        self.assertLess(engine._pending.initial_basis_dislocation, 0.0)
        self.assertLess(engine._pending.pulse_return_gap, 0.0)
        self.assertLess(engine._pending.oi_change_fraction, 0.0)

    def test_metric_availability_bar_is_not_part_of_pulse_and_scales_are_frozen(self):
        engine = warmed_dislocation("DOWN")
        observe_metric(engine, "DOWN")
        assert engine._pending is not None
        pending = engine._pending
        self.assertEqual(pending.metric_source_ns, 260 * MINUTE)
        self.assertEqual(pending.source.end_ns, 255 * MINUTE)
        self.assertAlmostEqual(pending.pulse_low, 95.4)
        completed = [item for item in engine._bars if item.ts_ns <= pending.metric_source_ns and item.has_spot]
        historical = completed[:-engine.config.metrics_interval_minutes]
        self.assertAlmostEqual(pending.frozen_atr, engine._futures_atr(historical, engine.config.atr_period))
        self.assertAlmostEqual(pending.frozen_spot_atr, engine._spot_atr(historical, engine.config.atr_period))

    def test_spot_leads_and_perpetual_counterflow_build_cost_after_buy_to_source_equilibrium(self):
        engine = warmed_dislocation("DOWN")
        observe_metric(engine, "DOWN")
        assert engine._pending is not None
        target = engine._pending.source.equilibrium
        result = engine.on_bar(bar(
            261, open_=95.6, high=97.0, low=95.5, close=96.8,
            spot_ohlc=(99.50, 99.85, 99.48, 99.80),
            volume=180.0, flow=0.30,
        ))
        self.assertIsNotNone(result.signal)
        assert result.signal is not None
        self.assertEqual(result.signal.side, "BUY")
        self.assertEqual(result.signal.branch, "REVERSAL")
        self.assertAlmostEqual(result.signal.target_price, target)
        self.assertLess(result.signal.stop_price, result.signal.entry_reference)
        self.assertGreater(result.signal.target_price, result.signal.entry_reference)
        self.assertGreaterEqual(result.signal.net_reward_to_risk, 1.2)
        self.assertEqual(result.signal.reason_code, "SPOT_LED_LIQUIDATION_AUCTION_REVERSION")

    def test_upside_dislocation_is_symmetric(self):
        engine = warmed_dislocation("UP")
        observe_metric(engine, "UP")
        assert engine._pending is not None
        target = engine._pending.source.equilibrium
        result = engine.on_bar(bar(
            261, open_=104.4, high=104.5, low=102.9, close=103.1,
            spot_ohlc=(100.50, 100.52, 100.10, 100.15),
            volume=180.0, flow=-0.30,
        ))
        self.assertIsNotNone(result.signal)
        assert result.signal is not None
        self.assertEqual(result.signal.side, "SELL")
        self.assertAlmostEqual(result.signal.target_price, target)
        self.assertGreater(result.signal.stop_price, result.signal.entry_reference)
        self.assertLess(result.signal.target_price, result.signal.entry_reference)

    def test_oi_and_spot_gap_admission_controls_are_independent(self):
        baseline = warmed_dislocation("DOWN")
        observe_metric(baseline, "DOWN", oi=100_001.0)
        self.assertIsNone(baseline._pending)
        no_oi = warmed_dislocation("DOWN", "no-oi")
        observe_metric(no_oi, "DOWN", oi=100_001.0)
        self.assertIsNotNone(no_oi._pending)

        baseline_follow = warmed_dislocation("DOWN", spot_follows=True)
        observe_metric(baseline_follow, "DOWN", spot_close=95.5)
        self.assertIsNone(baseline_follow._pending)
        no_gap = warmed_dislocation("DOWN", "no-spot-gap", spot_follows=True)
        observe_metric(no_gap, "DOWN", spot_close=95.5)
        self.assertIsNotNone(no_gap._pending)

    def test_no_spot_lead_removes_only_spot_structure_requirement(self):
        baseline = warmed_dislocation("DOWN")
        observe_metric(baseline, "DOWN")
        result = baseline.on_bar(bar(
            261, open_=95.6, high=97.0, low=95.5, close=96.8,
            spot_ohlc=(99.50, 99.54, 99.45, 99.52),
            volume=180.0, flow=0.30,
        ))
        self.assertIsNone(result.signal)

        control = warmed_dislocation("DOWN", "no-spot-lead")
        observe_metric(control, "DOWN")
        result = control.on_bar(bar(
            261, open_=95.6, high=97.0, low=95.5, close=96.8,
            spot_ohlc=(99.50, 99.54, 99.45, 99.52),
            volume=180.0, flow=0.30,
        ))
        self.assertIsNotNone(result.signal)

    def test_target_first_and_timeout_expire_without_chasing(self):
        target_first = warmed_dislocation("DOWN")
        observe_metric(target_first, "DOWN")
        result = target_first.on_bar(bar(
            261, open_=95.6, high=100.2, low=95.5, close=99.7,
            spot_ohlc=(99.5, 99.8, 99.4, 99.7), flow=0.30,
        ))
        self.assertIsNone(result.signal)
        self.assertTrue(any(
            event.reason_code == "SOURCE_AUCTION_EQUILIBRIUM_REACHED_BEFORE_ENTRY"
            for event in result.events
        ))

        timeout = warmed_dislocation("DOWN")
        observe_metric(timeout, "DOWN")
        last = None
        for i in range(261, 265):
            last = timeout.on_bar(bar(
                i, open_=95.5, high=95.8, low=95.3, close=95.6,
                spot_ohlc=(99.5, 99.6, 99.4, 99.5), flow=-0.05,
            ))
        assert last is not None
        self.assertIsNone(last.signal)
        self.assertTrue(any(
            event.reason_code == "SPOT_LED_REVERSAL_DID_NOT_CONFIRM_IN_TIME"
            for event in last.events
        ))


if __name__ == "__main__":
    unittest.main()
