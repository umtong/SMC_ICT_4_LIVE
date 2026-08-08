from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math

from router import BarObservation, FeatureObservation
from router_v4 import SymbolContext
import router_v6 as v6

STEP15 = v6.FIFTEEN_MINUTES_NS


def _feature(*, side: int = 1, oi: float = 0.002, flow: float = 0.35, efficiency: float = 0.20) -> FeatureObservation:
    return FeatureObservation(observed_time_ns=1, ready=True, flow_open_10s=side * flow, notional_open_10s_burst=2.0, flow_60s=side * flow, efficiency_60s=efficiency, oi_change_15m=oi, premium_z=0.0)


def _bar15(index: int, open_: float, high: float, low: float, close: float, volume: float = 100.0) -> BarObservation:
    return BarObservation(index * STEP15, open_, high, low, close, volume)


def _deep_context(*, shallow: bool = False) -> SymbolContext:
    bars = []
    price = 100.0
    for index in range(80):
        open_ = price
        close = price + 0.06
        bars.append(_bar15(index, open_, close + 0.12, open_ - 0.08, close, 100.0))
        price = close
    for _ in range(8):
        index = len(bars)
        open_ = price
        close = price + 0.55
        bars.append(_bar15(index, open_, close + 0.10, open_ - 0.05, close, 160.0))
        price = close
    changes = (-0.20, -0.15, -0.10) if shallow else (-1.00, -0.90, -0.65)
    for change in changes:
        index = len(bars)
        open_ = price
        close = price + change
        bars.append(_bar15(index, open_, max(open_, close) + 0.08, min(open_, close) - 0.12, close, 85.0))
        price = close
    index = len(bars)
    open_ = price - 0.05
    close = price + 0.75
    bars.append(_bar15(index, open_, close + 0.08, open_ - 0.08, close, 135.0))
    return SymbolContext("BTCUSDT", tuple(bars), 1.0, 2.0, 100.0)


def _deep_config() -> v6.V6Config:
    return v6.V6Config(min_trend_slope_atr=0.10, min_impulse_atr=1.0, max_impulse_atr=8.0, min_impulse_efficiency=0.20, min_impulse_volume_ratio=0.80, min_retrace_fraction=0.10, max_retrace_fraction=0.90, deep_pullback_target_r_floor=1.10)


def test_deep_pullback_uses_lower_value_for_long_not_shallow_fast_value():
    audit = v6.Counter()
    context = _deep_context()
    decision = v6._deep_value_price_candidate(context, breadth_by_side={1: 1.0, -1: 0.0}, config=_deep_config(), audit=audit)
    assert decision is not None
    diag = decision.diagnostics
    assert diag["deep_value"] == min(diag["anchored_vwap"], diag["trend_value_20"])
    assert decision.entry_reference == diag["deep_value"]
    assert decision.episode_ts < context.bars15[-1].ts_event
    assert diag["event_confirmation_separated"] is True


def test_shallow_retrace_is_rejected_when_it_never_touches_deep_value():
    audit = v6.Counter()
    decision = v6._deep_value_price_candidate(_deep_context(shallow=True), breadth_by_side={1: 1.0, -1: 0.0}, config=_deep_config(), audit=audit)
    assert decision is None
    assert audit["deep_reject_no_deep_value_touch"] >= 1


def test_deep_pullback_still_requires_positioning_sponsorship():
    audit = v6.Counter()
    price = v6._deep_value_price_candidate(_deep_context(), breadth_by_side={1: 1.0, -1: 0.0}, config=_deep_config(), audit=audit)
    assert price is not None
    assert v6._informed_deep_pullback(price, event_feature=_feature(oi=-0.003), confirmation_feature=_feature(), config=_deep_config(), audit=audit) is None


def _minute(ts: int, open_: float, high: float, low: float, close: float, volume: float = 100.0) -> BarObservation:
    return BarObservation(ts, open_, high, low, close, volume)


def _acd_fixture(*, c_failure: bool = False):
    session_start = int(datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    bars15 = []
    prior_start = session_start - 8 * v6.HOUR_NS
    for index in range(32):
        ts = prior_start + index * STEP15
        phase = math.sin(index / 5.0)
        bars15.append(_minute(ts, 100.0, 106.0, 96.0, 100.0 + 0.1 * phase, 100.0))
    for index in range(4):
        ts = session_start + index * STEP15 + 14 * v6.MINUTE_NS
        bars15.append(_minute(ts, 100.0, 101.0, 99.0, 100.2, 110.0))
    event15_ts = session_start + 74 * v6.MINUTE_NS
    bars15.append(_minute(event15_ts, 101.2, 102.0, 101.1, 101.8, 140.0))
    latest_ts = session_start + 89 * v6.MINUTE_NS
    if c_failure:
        bars15[-1] = _minute(event15_ts, 98.2, 98.4, 97.7, 97.9, 160.0)
        bars15.append(_minute(latest_ts, 98.45, 98.65, 97.8, 98.0, 145.0))
    else:
        bars15.append(_minute(latest_ts, 101.55, 102.2, 101.4, 102.0, 145.0))
    minutes = []
    for minute in range(60):
        ts = session_start + minute * v6.MINUTE_NS
        close = 100.0 + 0.1 * math.sin(minute / 6.0)
        high = 101.0 if minute == 0 else close + 0.12
        low = 99.0 if minute == 0 else close - 0.12
        minutes.append(_minute(ts, close, high, low, close, 100.0))
    for offset, close in enumerate((101.62, 101.72, 101.82), start=60):
        ts = session_start + offset * v6.MINUTE_NS
        minutes.append(_minute(ts, close - 0.05, close + 0.08, close - 0.10, close, 150.0))
    if c_failure:
        for offset in range(63, 70):
            ts = session_start + offset * v6.MINUTE_NS
            close = 100.8 - (offset - 62) * 0.25
            minutes.append(_minute(ts, close + 0.05, close + 0.10, close - 0.10, close, 140.0))
        for offset, close in enumerate((98.38, 98.28, 98.18), start=70):
            ts = session_start + offset * v6.MINUTE_NS
            minutes.append(_minute(ts, close + 0.05, close + 0.10, close - 0.10, close, 170.0))
        for offset in range(73, 90):
            ts = session_start + offset * v6.MINUTE_NS
            close = 98.0 + 0.02 * math.sin(offset)
            minutes.append(_minute(ts, close + 0.03, close + 0.10, close - 0.10, close, 120.0))
    else:
        for offset in range(63, 90):
            ts = session_start + offset * v6.MINUTE_NS
            close = 101.8 + 0.05 * math.sin(offset)
            minutes.append(_minute(ts, close - 0.03, close + 0.10, close - 0.10, close, 120.0))
    context = SymbolContext("BTCUSDT", tuple(bars15), 1.0, 1.5 if not c_failure else -1.5, 100.0)
    return session_start, context, tuple(minutes)


def test_acd_a_requires_persistent_three_minute_establishment_and_far_side_b_stop():
    session_start, context, minutes = _acd_fixture()
    event_ts = session_start + 62 * v6.MINUTE_NS
    features = {event_ts: _feature(side=1, oi=0.002, flow=0.4)}
    audit = v6.Counter()
    a_decision, c_decision = v6._acd_candidates(context, minute_bars=minutes, breadth_by_side={1: 0.75, -1: 0.0}, feature_at=lambda _symbol, ts: features.get(ts, _feature(side=1)), confirmation_feature=_feature(side=1, oi=0.001, flow=0.4), config=replace(v6.V6Config(), session_range_lookback=1, acd_a_target_r_floor=1.0), audit=audit)
    assert a_decision is not None
    assert c_decision is None
    assert a_decision.state == "ACD_A_ESTABLISHMENT_RETEST"
    assert a_decision.diagnostics["persistence_minutes"] == 3
    assert a_decision.stop_reference < a_decision.diagnostics["opening_range_low"]
    assert a_decision.episode_ts == event_ts


def test_acd_a_is_not_established_by_a_single_breakout_minute():
    _session_start, context, minutes = _acd_fixture()
    reduced_rows = []
    kept_breakout = False
    for item in minutes:
        after_open = item.ts_event >= minutes[60].ts_event
        if after_open and item.close > 101.5:
            if kept_breakout:
                reduced_rows.append(BarObservation(item.ts_event, 101.15, 101.35, 101.05, 101.20, item.volume))
                continue
            kept_breakout = True
        reduced_rows.append(item)
    audit = v6.Counter()
    a_decision, c_decision = v6._acd_candidates(context, minute_bars=tuple(reduced_rows), breadth_by_side={1: 1.0, -1: 0.0}, feature_at=lambda _symbol, _ts: _feature(side=1), confirmation_feature=_feature(side=1), config=replace(v6.V6Config(), session_range_lookback=1, acd_a_target_r_floor=1.0), audit=audit)
    assert a_decision is None
    assert c_decision is None


def test_acd_c_requires_a_prior_established_a_and_opposite_persistence():
    session_start, context, minutes = _acd_fixture(c_failure=True)
    a_event = session_start + 62 * v6.MINUTE_NS
    c_event = session_start + 72 * v6.MINUTE_NS
    features = {a_event: _feature(side=1, oi=0.002, flow=0.4), c_event: _feature(side=-1, oi=-0.003, flow=0.4)}
    audit = v6.Counter()
    a_decision, c_decision = v6._acd_candidates(context, minute_bars=minutes, breadth_by_side={1: 0.0, -1: 0.75}, feature_at=lambda _symbol, ts: features.get(ts, _feature(side=-1, oi=-0.003, flow=0.4)), confirmation_feature=_feature(side=-1, oi=0.0, flow=0.4), config=replace(v6.V6Config(), session_range_lookback=1, acd_c_target_r_floor=0.90), audit=audit)
    assert a_decision is None
    assert c_decision is not None
    assert c_decision.state == "ACD_C_FAILED_A_REVERSAL"
    assert c_decision.diagnostics["first_a_side"] == 1
    assert c_decision.episode_ts == c_event


def test_acd_c_rejects_opposite_move_without_transition_flow_or_oi_flush():
    _session_start, context, minutes = _acd_fixture(c_failure=True)
    audit = v6.Counter()
    _a, c_decision = v6._acd_candidates(context, minute_bars=minutes, breadth_by_side={1: 0.0, -1: 0.75}, feature_at=lambda _symbol, _ts: _feature(side=-1, oi=0.001, flow=0.01), confirmation_feature=_feature(side=-1, oi=0.0, flow=0.4), config=replace(v6.V6Config(), session_range_lookback=1, acd_c_target_r_floor=1.0), audit=audit)
    assert c_decision is None
    assert audit["acd_c_reject_no_failed_a_transition"] >= 1


def test_route_returns_audit_even_when_no_symbol_is_actionable():
    context = _deep_context(shallow=True)
    minute_bars = []
    for item in context.bars15:
        for offset in range(15):
            minute_bars.append(BarObservation(item.ts_event + offset * v6.MINUTE_NS, item.open, item.high, item.low, item.close, item.volume / 15.0))
    winner, decisions, audit = v6.route_v6_universe(minute_bars_by_symbol={"BTCUSDT": minute_bars}, confirmation_features_by_symbol={"BTCUSDT": _feature()}, feature_at=lambda _symbol, _ts: _feature(), config=replace(_deep_config(), price=replace(_deep_config().price, min_completed_15m_bars=60)))
    assert isinstance(audit, dict)
    assert audit
    assert winner is None or winner.symbol in decisions
