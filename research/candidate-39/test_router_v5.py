from __future__ import annotations

from datetime import datetime, timezone

from router import BarObservation, FeatureObservation, RouteDecision
from router_v4 import SymbolContext
import router_v5 as v5


def _feature(
    *,
    side: int = 1,
    oi: float = 0.002,
    flow: float = 0.35,
    eff: float = 0.20,
    burst: float = 2.0,
    premium: float = 0.0,
) -> FeatureObservation:
    return FeatureObservation(
        observed_time_ns=1,
        ready=True,
        flow_open_10s=side * flow,
        notional_open_10s_burst=burst,
        flow_60s=side * flow,
        efficiency_60s=eff,
        oi_change_15m=oi,
        premium_z=premium,
    )


def _decision(
    state: str,
    side: int,
    *,
    entry: float = 100.0,
    stop: float = 99.0,
    target: float = 104.0,
    diagnostics: dict | None = None,
) -> RouteDecision:
    if side < 0:
        stop, target = 101.0, 96.0
    return RouteDecision(
        symbol="BTCUSDT",
        state=state,
        side=side,
        score=4.0,
        expected_target_r=4.0,
        atr=1.0,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=10,
        reasons=("TEST",),
        diagnostics=diagnostics or {},
    )


def _context(
    side: int = 1,
    latest_close: float = 100.5,
    return_4h: float = -1.0,
) -> SymbolContext:
    bars = tuple(
        BarObservation(i, 100.0, 101.0, 99.0, latest_close if i == 119 else 100.0, 100.0)
        for i in range(120)
    )
    return SymbolContext("BTCUSDT", bars, 1.0, return_4h, 100.0)


def test_sponsored_pullback_requires_oi_and_two_stage_flow():
    base = _decision("FIRST_PULLBACK_CONTINUATION", 1, diagnostics={"impulse_atr": 3.0})
    result = v5._sponsored_pullback(base, _feature(), _feature(), v5.InformedRouterConfig())
    assert result is not None
    assert result.state == "SPONSORED_FIRST_PULLBACK"
    assert result.diagnostics["event_oi_change_15m"] > 0.0


def test_sponsored_pullback_rejects_liquidation_impulse():
    base = _decision("FIRST_PULLBACK_CONTINUATION", 1, diagnostics={"impulse_atr": 3.0})
    assert v5._sponsored_pullback(
        base,
        _feature(oi=-0.003),
        _feature(),
        v5.InformedRouterConfig(),
    ) is None


def test_sponsored_pullback_rejects_climax_and_weak_confirmation():
    base = _decision("FIRST_PULLBACK_CONTINUATION", 1, diagnostics={"impulse_atr": 3.0})
    cfg = v5.InformedRouterConfig()
    assert v5._sponsored_pullback(base, _feature(burst=20.0), _feature(), cfg) is None
    assert v5._sponsored_pullback(base, _feature(), _feature(flow=0.02), cfg) is None


def test_failed_level_requires_leverage_flush_and_flow_flip():
    base = _decision(
        "FAILED_LEVEL_REACCEPTANCE",
        1,
        entry=100.1,
        stop=99.4,
        target=104.0,
        diagnostics={"attacked_level": 100.0, "reference": "PRIOR_UTC_DAY"},
    )
    event = _feature(side=-1, oi=-0.003, flow=0.45)
    confirmation = _feature(side=1, oi=0.0002, flow=0.45, eff=0.25)
    result = v5._informed_failed_level(
        base,
        _context(latest_close=100.5, return_4h=-1.0),
        event,
        confirmation,
        median_return=0.0,
        breadth_by_side={1: 0.0, -1: 0.75},
        config=v5.InformedRouterConfig(),
    )
    assert result is not None
    assert result.state == "LIQUIDATION_FAILURE_REACCEPTANCE"
    assert result.stop_reference <= 99.3
    assert "PRIOR_UTC_DAY" in result.diagnostics["episode_key"]


def test_failed_level_rejects_no_flush_or_shallow_reacceptance():
    base = _decision(
        "FAILED_LEVEL_REACCEPTANCE",
        1,
        entry=100.1,
        stop=99.4,
        target=104.0,
        diagnostics={"attacked_level": 100.0, "reference": "PRIOR_UTC_DAY"},
    )
    cfg = v5.InformedRouterConfig()
    assert v5._informed_failed_level(
        base,
        _context(latest_close=100.5),
        _feature(side=-1, oi=0.001),
        _feature(side=1),
        0.0,
        {1: 0.0},
        cfg,
    ) is None
    assert v5._informed_failed_level(
        base,
        _context(latest_close=100.05),
        _feature(side=-1, oi=-0.003),
        _feature(side=1),
        0.0,
        {1: 0.0},
        cfg,
    ) is None


def test_pending_parent_is_cancelled_before_fill_after_stop_touch():
    bar = BarObservation(1, 100.0, 102.0, 98.0, 100.0, 1.0)
    assert v5.pending_setup_invalidated(bar, 1, 99.0)
    assert v5.pending_setup_invalidated(bar, -1, 101.0)


def test_operational_horizon_matches_non_scalping_minimum():
    ts = int(datetime(2026, 7, 14, 23, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    assert 30.0 < v5.minutes_to_next_funding(ts) < 32.0


def test_opening_range_acceptance_uses_separate_event_and_retest():
    start = int(datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    step = 15 * v5.MINUTE_NS
    bars = []
    for i in range(32):
        bars.append(BarObservation(start + i * step, 99.8, 100.4, 99.4, 100.0, 100.0))
    session = start + 8 * v5.HOUR_NS
    opening = [
        BarObservation(session + i * step, 100.0, 101.0, 99.0, 100.2, 110.0)
        for i in range(4)
    ]
    event = BarObservation(session + 4 * step, 100.9, 101.45, 100.8, 101.35, 160.0)
    confirmation = BarObservation(session + 5 * step, 101.25, 101.55, 101.05, 101.40, 140.0)
    bars.extend(opening + [event, confirmation])
    context = SymbolContext("BTCUSDT", tuple(bars), 1.0, 1.0, 100.0)
    features = {
        event.ts_event: _feature(side=1, oi=0.002, flow=0.4),
        confirmation.ts_event: _feature(side=1, oi=0.001, flow=0.4),
    }
    result = v5._opening_range_candidate(
        context,
        breadth_by_side={1: 0.75, -1: 0.0},
        feature_at=lambda _symbol, ts: features.get(ts, FeatureObservation(0, ready=False)),
        confirmation_feature=features[confirmation.ts_event],
        config=v5.InformedRouterConfig(),
    )
    assert result is not None
    assert result.state == "OPENING_RANGE_ACCEPTANCE_RETEST"
    assert result.episode_ts == event.ts_event
    assert result.entry_reference < confirmation.close


def test_opening_range_rejects_unsponsored_single_asset_break():
    start = int(datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    step = 15 * v5.MINUTE_NS
    bars = [
        BarObservation(start + i * step, 99.8, 100.4, 99.4, 100.0, 100.0)
        for i in range(32)
    ]
    session = start + 8 * v5.HOUR_NS
    bars.extend([
        BarObservation(session + i * step, 100, 101, 99, 100.2, 110)
        for i in range(4)
    ])
    event = BarObservation(session + 4 * step, 100.9, 101.45, 100.8, 101.35, 160)
    confirmation = BarObservation(session + 5 * step, 101.25, 101.55, 101.05, 101.40, 140)
    bars.extend([event, confirmation])
    context = SymbolContext("BTCUSDT", tuple(bars), 1.0, 1.0, 100.0)
    weak = _feature(side=1, oi=0.0, flow=0.01)
    assert v5._opening_range_candidate(
        context,
        breadth_by_side={1: 0.25, -1: 0.0},
        feature_at=lambda _symbol, _ts: weak,
        confirmation_feature=weak,
        config=v5.InformedRouterConfig(),
    ) is None
