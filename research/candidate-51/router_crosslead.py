"""Causal BTC/ETH leader -> SOL/XRP next-bar transition policy.

External cryptocurrency lead-lag and "seesaw" research is used only as a
hypothesis generator.  At every completed five-minute boundary this policy:

* builds leader returns from BTC and ETH;
* estimates target[t+1] ~ leader[t] only from pairs fully known now;
* requires the beta sign to agree in both halves of the rolling window;
* requires a statistically material relationship and an exceptional current
  leader shock;
* trades only when the predicted next-bar move clears costs and structural
  reward space.

No fixed claim that spot, futures, BTC, or ETH always leads is assumed.  Seesaw
and follow modes are evaluated as independent causal families before any union.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

from router_picasso import BarObservation, _aggregate_complete, _atr

SEESAW_STATE = "BTC_ETH_LEADER_SEESAW"
FOLLOW_STATE = "BTC_ETH_LEADER_FOLLOW"
SMA_OFFSET_STATE = SEESAW_STATE
UNRESOLVED = "UNRESOLVED"
_EPS = 1e-12
_TARGET_PRIORITY = {"SOLUSDT": 0, "XRPUSDT": 1}


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = True


@dataclass(frozen=True, slots=True)
class RouteConfig:
    # Reused execution-shell fields.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    crosslead_mode: str = "seesaw"  # seesaw | follow | adaptive
    crosslead_bucket_minutes: int = 5
    crosslead_train_pairs: int = 288
    crosslead_min_pairs: int = 144
    crosslead_min_abs_beta: float = 0.10
    crosslead_min_abs_tstat: float = 2.00
    crosslead_min_shock_z: float = 2.00
    crosslead_min_predicted_bps: float = 30.0
    crosslead_atr_period: int = 14
    crosslead_stop_buffer_atr: float = 0.10
    crosslead_min_reward_r: float = 1.25

    # Generic legacy compatibility fields.
    sma_offset_period: int = 8
    sma_offset_low: float = 0.960
    sma_offset_high: float = 1.012
    sma_trend_fast: int = 20
    sma_trend_slow: int = 25
    sma_stop_min_fraction: float = 0.0075
    sma_stop_max_fraction: float = 0.1000
    sma_stop_atr_buffer: float = 0.50
    sma_structural_lookback: int = 6
    sma_min_reward_r: float = 1.00


@dataclass(frozen=True, slots=True)
class RouteDecision:
    symbol: str
    state: str
    side: int
    score: float
    entry_reference: float
    stop_reference: float
    objective_reference: float
    episode_ts: int
    reasons: tuple[str, ...] = ()
    diagnostics: Mapping[str, float | int | str] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.side in (-1, 1) and self.state != UNRESOLVED


@dataclass(frozen=True, slots=True)
class Relationship:
    samples: int
    beta: float
    alpha: float
    correlation: float
    tstat: float
    beta_first: float
    beta_second: float
    x_std: float


def _unresolved(
    symbol: str,
    reason: str,
    episode_ts: int = 0,
    diagnostics: Mapping[str, float | int | str] | None = None,
) -> RouteDecision:
    return RouteDecision(
        symbol=symbol,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(episode_ts),
        reasons=(reason,),
        diagnostics=dict(diagnostics or {}),
    )


def _mean(values: Sequence[float]) -> float:
    return sum(float(value) for value in values) / len(values)


def _beta(x: Sequence[float], y: Sequence[float]) -> tuple[float, float, float, float]:
    if len(x) != len(y) or len(x) < 3:
        return math.nan, math.nan, math.nan, math.nan
    mx = _mean(x)
    my = _mean(y)
    dx = [float(value) - mx for value in x]
    dy = [float(value) - my for value in y]
    var_x = sum(value * value for value in dx)
    var_y = sum(value * value for value in dy)
    if var_x <= _EPS or var_y <= _EPS:
        return math.nan, math.nan, math.nan, math.nan
    covariance = sum(a * b for a, b in zip(dx, dy))
    beta = covariance / var_x
    alpha = my - beta * mx
    correlation = covariance / math.sqrt(var_x * var_y)
    correlation = max(-0.999999, min(0.999999, correlation))
    tstat = correlation * math.sqrt((len(x) - 2) / max(1.0 - correlation * correlation, _EPS))
    return beta, alpha, correlation, tstat


def fit_relationship(
    leader_returns: Sequence[float],
    target_returns: Sequence[float],
    max_pairs: int,
    min_pairs: int,
) -> Relationship | None:
    """Fit target[t+1] on leader[t] using only completed pairs."""
    if len(leader_returns) != len(target_returns) or len(leader_returns) < min_pairs + 2:
        return None
    x_all = list(leader_returns[:-1])
    y_all = list(target_returns[1:])
    if max_pairs > 0:
        x_all = x_all[-max_pairs:]
        y_all = y_all[-max_pairs:]
    if len(x_all) < min_pairs:
        return None
    beta, alpha, correlation, tstat = _beta(x_all, y_all)
    split = len(x_all) // 2
    beta_first, _, _, _ = _beta(x_all[:split], y_all[:split])
    beta_second, _, _, _ = _beta(x_all[split:], y_all[split:])
    if not all(math.isfinite(value) for value in (
        beta, alpha, correlation, tstat, beta_first, beta_second
    )):
        return None
    mx = _mean(x_all)
    x_std = math.sqrt(sum((float(value) - mx) ** 2 for value in x_all) / len(x_all))
    if not math.isfinite(x_std) or x_std <= _EPS:
        return None
    return Relationship(
        samples=len(x_all),
        beta=beta,
        alpha=alpha,
        correlation=correlation,
        tstat=tstat,
        beta_first=beta_first,
        beta_second=beta_second,
        x_std=x_std,
    )


def _aligned_buckets(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    bucket_minutes: int,
    symbols: Sequence[str],
) -> tuple[list[int], dict[str, list[BarObservation]]]:
    maps: dict[str, dict[int, BarObservation]] = {}
    for symbol in symbols:
        candles = _aggregate_complete(bars_by_symbol.get(symbol, ()), bucket_minutes)
        maps[symbol] = {int(candle.ts_event): candle for candle in candles}
    if not maps:
        return [], {}
    common = set.intersection(*(set(items) for items in maps.values()))
    timestamps = sorted(common)
    aligned = {
        symbol: [maps[symbol][timestamp] for timestamp in timestamps]
        for symbol in symbols
    }
    return timestamps, aligned


def _log_returns(candles: Sequence[BarObservation]) -> list[float]:
    output: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        p0 = float(previous.close)
        p1 = float(current.close)
        output.append(math.log(p1 / p0) if p0 > 0.0 and p1 > 0.0 else math.nan)
    return output


def _decision_for_target(
    symbol: str,
    aligned: Mapping[str, Sequence[BarObservation]],
    timestamps: Sequence[int],
    config: RouteConfig,
) -> RouteDecision:
    if symbol not in aligned or len(timestamps) < int(config.crosslead_min_pairs) + 3:
        return _unresolved(symbol, "CROSSLEAD_HISTORY_NOT_READY", timestamps[-1] if timestamps else 0)
    btc_returns = _log_returns(aligned["BTCUSDT"])
    eth_returns = _log_returns(aligned["ETHUSDT"])
    target_returns = _log_returns(aligned[symbol])
    if not (
        len(btc_returns) == len(eth_returns) == len(target_returns)
        and all(math.isfinite(value) for value in btc_returns[-int(config.crosslead_train_pairs) - 2:])
        and all(math.isfinite(value) for value in eth_returns[-int(config.crosslead_train_pairs) - 2:])
        and all(math.isfinite(value) for value in target_returns[-int(config.crosslead_train_pairs) - 2:])
    ):
        return _unresolved(symbol, "CROSSLEAD_NONFINITE_RETURN", timestamps[-1])
    leader_returns = [0.5 * (btc + eth) for btc, eth in zip(btc_returns, eth_returns)]
    relationship = fit_relationship(
        leader_returns,
        target_returns,
        int(config.crosslead_train_pairs),
        int(config.crosslead_min_pairs),
    )
    if relationship is None:
        return _unresolved(symbol, "CROSSLEAD_RELATIONSHIP_NOT_READY", timestamps[-1])

    mode = str(config.crosslead_mode).strip().lower()
    sign_stable = relationship.beta_first * relationship.beta_second > 0.0
    diagnostics: dict[str, float | int | str] = {
        "mode": mode,
        "samples": relationship.samples,
        "beta": relationship.beta,
        "alpha": relationship.alpha,
        "correlation": relationship.correlation,
        "tstat": relationship.tstat,
        "beta_first": relationship.beta_first,
        "beta_second": relationship.beta_second,
        "sign_stable": int(sign_stable),
        "x_std": relationship.x_std,
    }
    if not sign_stable:
        return _unresolved(symbol, "CROSSLEAD_BETA_SIGN_UNSTABLE", timestamps[-1], diagnostics)
    if abs(relationship.beta) < float(config.crosslead_min_abs_beta):
        return _unresolved(symbol, "CROSSLEAD_BETA_TOO_SMALL", timestamps[-1], diagnostics)
    if abs(relationship.tstat) < float(config.crosslead_min_abs_tstat):
        return _unresolved(symbol, "CROSSLEAD_TSTAT_TOO_SMALL", timestamps[-1], diagnostics)
    if mode == "seesaw" and not (
        relationship.beta < 0.0
        and relationship.beta_first < 0.0
        and relationship.beta_second < 0.0
        and relationship.tstat < 0.0
    ):
        return _unresolved(symbol, "CROSSLEAD_NOT_STABLE_SEESAW", timestamps[-1], diagnostics)
    if mode == "follow" and not (
        relationship.beta > 0.0
        and relationship.beta_first > 0.0
        and relationship.beta_second > 0.0
        and relationship.tstat > 0.0
    ):
        return _unresolved(symbol, "CROSSLEAD_NOT_STABLE_FOLLOW", timestamps[-1], diagnostics)
    if mode not in {"seesaw", "follow", "adaptive"}:
        return _unresolved(symbol, "CROSSLEAD_UNKNOWN_MODE", timestamps[-1], diagnostics)

    current_leader = float(leader_returns[-1])
    shock_z = current_leader / relationship.x_std
    predicted = relationship.alpha + relationship.beta * current_leader
    predicted_bps = predicted * 10_000.0
    diagnostics.update({
        "current_leader_return": current_leader,
        "leader_shock_z": shock_z,
        "predicted_target_return": predicted,
        "predicted_target_bps": predicted_bps,
    })
    if abs(shock_z) < float(config.crosslead_min_shock_z):
        return _unresolved(symbol, "CROSSLEAD_LEADER_SHOCK_TOO_SMALL", timestamps[-1], diagnostics)
    if abs(predicted_bps) < float(config.crosslead_min_predicted_bps):
        return _unresolved(symbol, "CROSSLEAD_PREDICTED_MOVE_BELOW_COST_FLOOR", timestamps[-1], diagnostics)
    side = 1 if predicted > 0.0 else -1

    target_candles = aligned[symbol]
    atr_values = _atr(target_candles, int(config.crosslead_atr_period))
    atr = float(atr_values[-1]) if atr_values else math.nan
    if not math.isfinite(atr) or atr <= 0.0:
        return _unresolved(symbol, "CROSSLEAD_ATR_NOT_READY", timestamps[-1], diagnostics)
    current = target_candles[-1]
    previous = target_candles[-2]
    entry = float(current.close)
    buffer = float(config.crosslead_stop_buffer_atr) * atr
    if side > 0:
        stop = min(float(current.low), float(previous.low)) - buffer
        objective = entry * (1.0 + predicted)
        reward = objective - entry
    else:
        stop = max(float(current.high), float(previous.high)) + buffer
        objective = entry * (1.0 + predicted)
        reward = entry - objective
    risk = abs(entry - stop)
    reward_r = reward / risk if risk > _EPS else math.nan
    diagnostics.update({
        "atr": atr,
        "entry": entry,
        "stop": stop,
        "objective": objective,
        "risk_distance": risk,
        "reward_distance": reward,
        "reward_r": reward_r,
    })
    valid = (
        0.0 < stop < entry < objective
        if side > 0
        else 0.0 < objective < entry < stop
    )
    if not valid or not math.isfinite(reward_r):
        return _unresolved(symbol, "CROSSLEAD_GEOMETRY_INVALID", timestamps[-1], diagnostics)
    if reward_r < float(config.crosslead_min_reward_r):
        return _unresolved(symbol, "CROSSLEAD_REWARD_SPACE_EXHAUSTED", timestamps[-1], diagnostics)

    state = SEESAW_STATE if relationship.beta < 0.0 else FOLLOW_STATE
    score = (
        abs(relationship.tstat)
        + abs(shock_z)
        + abs(predicted_bps) / max(float(config.crosslead_min_predicted_bps), _EPS)
        + min(3.0, abs(relationship.beta))
    )
    return RouteDecision(
        symbol=symbol,
        state=state,
        side=side,
        score=score,
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=int(timestamps[-1]),
        reasons=(
            "CAUSAL_COMPLETED_LEADER_TARGET_PAIRS",
            "ROLLING_BETA_SIGN_STABLE_IN_BOTH_HALVES",
            "STATISTICALLY_MATERIAL_LEAD_LAG",
            "EXCEPTIONAL_CURRENT_BTC_ETH_SHOCK",
            "PREDICTED_MOVE_CLEARS_COST_FLOOR",
            "NEXT_BUCKET_OBJECTIVE_WITH_STRUCTURAL_INVALIDATION",
        ),
        diagnostics=diagnostics,
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    return _unresolved(symbol, "CROSSLEAD_REQUIRES_UNIVERSE_CONTEXT", bars[-1].ts_event if bars else 0)


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    del features_by_symbol
    required = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    timestamps, aligned = _aligned_buckets(
        bars_by_symbol,
        int(config.crosslead_bucket_minutes),
        required,
    )
    decisions = {
        "BTCUSDT": _unresolved("BTCUSDT", "CROSSLEAD_LEADER_NOT_TRADED", timestamps[-1] if timestamps else 0),
        "ETHUSDT": _unresolved("ETHUSDT", "CROSSLEAD_LEADER_NOT_TRADED", timestamps[-1] if timestamps else 0),
        "SOLUSDT": _decision_for_target("SOLUSDT", aligned, timestamps, config),
        "XRPUSDT": _decision_for_target("XRPUSDT", aligned, timestamps, config),
    }
    candidates = [decision for decision in decisions.values() if decision.actionable]
    candidates.sort(key=lambda decision: (
        -decision.score,
        _TARGET_PRIORITY.get(decision.symbol, 99),
        decision.episode_ts,
    ))
    return (candidates[0] if candidates else None), decisions


__all__ = [
    "BarObservation",
    "FOLLOW_STATE",
    "FeatureObservation",
    "Relationship",
    "RouteConfig",
    "RouteDecision",
    "SEESAW_STATE",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "classify_symbol",
    "fit_relationship",
    "route_universe",
]
