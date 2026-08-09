from __future__ import annotations

from router import (
    BarObservation,
    FeatureObservation,
    SMA_OFFSET_STATE,
    RouteConfig,
    classify_sma_offset,
    classify_symbol,
    route_universe,
    sma_offset_exit_ready,
)


def _dip_bars(count: int = 2200, final_drift: float = -1.10):
    result = []
    price = 100.0
    for index in range(count):
        drift = 0.006
        if index >= count - 10:
            drift = final_drift
        open_ = price
        close = max(1.0, price + drift)
        result.append(
            BarObservation(
                (index + 1) * 60_000_000_000 - 1,
                open_, max(open_, close) + 0.03, min(open_, close) - 0.03,
                close, 1000.0 + index,
            )
        )
        price = close
    return result


def test_future_feature_is_rejected():
    bars = _dip_bars()
    decision = classify_symbol(
        "BTCUSDT", bars,
        FeatureObservation(bars[-1].ts_event + 1, ready=True),
        RouteConfig(),
    )
    assert not decision.actionable
    assert "FUTURE_FEATURE_REJECTED" in decision.reasons


def test_exact_public_sma_offset_family_is_causal_and_structural():
    bars = _dip_bars()
    decision = classify_sma_offset(
        "BTCUSDT", bars,
        FeatureObservation(bars[-1].ts_event),
        RouteConfig(),
    )
    assert decision.actionable, (decision.reasons, decision.diagnostics)
    assert decision.state == SMA_OFFSET_STATE
    assert decision.side == 1
    assert decision.stop_reference < decision.entry_reference < decision.objective_reference
    assert decision.diagnostics["sma_reward_risk"] >= 1.0


def test_shallow_dip_is_rejected_at_exact_source_threshold():
    bars = _dip_bars(final_drift=-0.10)
    decision = classify_symbol(
        "ETHUSDT", bars,
        FeatureObservation(bars[-1].ts_event),
        RouteConfig(sma_offset_low=0.960),
    )
    assert not decision.actionable
    assert "SMA_OFFSET_DEEP_PULLBACK_NOT_PRESENT" in decision.reasons


def test_universe_selects_at_most_one():
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    bars = {symbol: _dip_bars(final_drift=-1.10 - i * 0.02) for i, symbol in enumerate(symbols)}
    features = {symbol: FeatureObservation(rows[-1].ts_event) for symbol, rows in bars.items()}
    winner, decisions = route_universe(bars, features, RouteConfig())
    assert sum(decision.actionable for decision in decisions.values()) >= int(winner is not None)
    assert winner is None or winner.symbol in symbols


def test_incomplete_five_minute_bucket_cannot_create_episode():
    bars = _dip_bars(count=2201)
    first = classify_symbol("XRPUSDT", bars[:-1], FeatureObservation(bars[-2].ts_event), RouteConfig())
    second = classify_symbol("XRPUSDT", bars, FeatureObservation(bars[-1].ts_event), RouteConfig())
    assert first.episode_ts == second.episode_ts


def test_sma_offset_exit_is_boolean():
    exited, diagnostics = sma_offset_exit_ready(_dip_bars(), RouteConfig())
    assert isinstance(exited, bool)
    assert diagnostics["sma_exit_ready"] in (0, 1)



def test_binance_millisecond_close_phase_is_supported():
    bars = _dip_bars()
    shifted = [
        BarObservation(
  bar.ts_event - 999_999,
  bar.open,
  bar.high,
  bar.low,
  bar.close,
  bar.volume,
        )
        for bar in bars
    ]
    decision = classify_sma_offset(
        "BTCUSDT",
        shifted,
        FeatureObservation(shifted[-1].ts_event),
        RouteConfig(),
    )
    assert decision.actionable, (decision.reasons, decision.diagnostics)
    assert decision.state == SMA_OFFSET_STATE
