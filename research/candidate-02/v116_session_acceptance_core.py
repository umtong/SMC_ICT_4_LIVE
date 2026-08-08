"""Causal completed-session acceptance continuation for candidate-02 v116.

This is not a looser form of v115 reversal.  It is a mutually exclusive auction
outcome.  Once the first boundary of an immutable completed session range is
consumed, the scenario can proceed only if completed bars accept price outside
that boundary, initiative displacement leaves a directional FVG, and a later
FVG retest holds outside the old range.

The profit objective is never a fitted multiple.  It is the nearest untouched
external liquidity level already knowable at the initial boundary break:

* previous completed UTC-day high/low,
* another completed session boundary, or
* a causally confirmed 15-minute swing high/low.

Signal construction only.  NautilusTrader remains the sole execution,
commission, position, risk and NAV engine.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from typing import Any, Mapping

import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, _true_range, cost_after_reward_risk
from v111_liquidity_sweep_core import _latest_feature, _normalize, _session_label, build_state
from v113_persistent_pool_router_core import _directional_fvg
from v115_session_range_sweep_core import (
    SessionRangeSweepConfig,
    _CompletedRange,
    _build_completed_ranges,
)

UTC = "UTC"


@dataclass(frozen=True, slots=True)
class SessionAcceptanceConfig(SessionRangeSweepConfig):
    acceptance_window_minutes: int = 12
    acceptance_closes: int = 2
    acceptance_buffer_atr: float = 0.05
    bos_beyond_breakout_atr: float = 0.02
    target_timeframe_minutes: int = 15
    target_pivot_span_bars: int = 1
    target_level_max_age_minutes: int = 2880

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "SessionAcceptanceConfig":
        data = dict(values)
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v116 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        SessionRangeSweepConfig.__post_init__(self)
        if self.liquidity_source not in {"ASIA", "LONDON"}:
            raise ValueError("v116 currently isolates ASIA or LONDON completed ranges")
        if self.acceptance_window_minutes < self.acceptance_closes:
            raise ValueError("v116 acceptance window is shorter than required closes")
        if self.acceptance_closes <= 0:
            raise ValueError("v116 acceptance closes must be positive")
        if self.acceptance_buffer_atr < 0 or self.bos_beyond_breakout_atr < 0:
            raise ValueError("v116 acceptance/BOS buffers cannot be negative")
        if self.target_timeframe_minutes < 5:
            raise ValueError("v116 target timeframe is too short")
        if self.target_pivot_span_bars < 1:
            raise ValueError("v116 target pivot span must be positive")
        if self.target_level_max_age_minutes <= self.target_timeframe_minutes:
            raise ValueError("v116 target level lifetime is too short")


@dataclass(frozen=True, slots=True)
class _ExternalLevel:
    level_id: str
    kind: str
    price: float
    source: str
    formed_time: pd.Timestamp
    available_time: pd.Timestamp

    def as_dict(self) -> dict[str, Any]:
        return {
            "level_id": self.level_id,
            "kind": self.kind,
            "price": self.price,
            "source": self.source,
            "formed_time_ns": int(self.formed_time.value),
            "available_time_ns": int(self.available_time.value),
        }


_LAST_DIAGNOSTICS: dict[str, Any] = {"summary": {}, "examples": {}}


def get_last_scenario_diagnostics() -> dict[str, Any]:
    return {
        "summary": dict(_LAST_DIAGNOSTICS.get("summary", {})),
        "examples": {
            str(key): list(values)
            for key, values in dict(_LAST_DIAGNOSTICS.get("examples", {})).items()
        },
    }


def _build_htf_swing_levels(
    raw: pd.DataFrame,
    *,
    timeframe_minutes: int,
    span: int,
) -> list[_ExternalLevel]:
    rule = f"{timeframe_minutes}min"
    grouped = raw.resample(rule, label="right", closed="right")
    htf = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    counts = grouped["close"].count()
    htf = htf.loc[counts >= timeframe_minutes].dropna()
    levels: list[_ExternalLevel] = []
    if len(htf) < 2 * span + 1:
        return levels
    for position in range(span, len(htf) - span):
        window = htf.iloc[position - span : position + span + 1]
        pivot_time = htf.index[position]
        available_time = htf.index[position + span]
        pivot_high = float(htf.iloc[position]["high"])
        pivot_low = float(htf.iloc[position]["low"])
        if math.isclose(pivot_high, float(window["high"].max()), rel_tol=0.0, abs_tol=1e-12):
            latest_equal = window.index[window["high"].map(lambda value: math.isclose(float(value), pivot_high, rel_tol=0.0, abs_tol=1e-12))][-1]
            if latest_equal == pivot_time:
                levels.append(
                    _ExternalLevel(
                        level_id=f"HTF-HIGH-{int(pivot_time.value)}",
                        kind="HIGH",
                        price=pivot_high,
                        source=f"CONFIRMED_{timeframe_minutes}M_SWING",
                        formed_time=pivot_time,
                        available_time=available_time,
                    )
                )
        if math.isclose(pivot_low, float(window["low"].min()), rel_tol=0.0, abs_tol=1e-12):
            latest_equal = window.index[window["low"].map(lambda value: math.isclose(float(value), pivot_low, rel_tol=0.0, abs_tol=1e-12))][-1]
            if latest_equal == pivot_time:
                levels.append(
                    _ExternalLevel(
                        level_id=f"HTF-LOW-{int(pivot_time.value)}",
                        kind="LOW",
                        price=pivot_low,
                        source=f"CONFIRMED_{timeframe_minutes}M_SWING",
                        formed_time=pivot_time,
                        available_time=available_time,
                    )
                )
    return levels


def _build_fixed_boundary_levels(
    raw: pd.DataFrame,
    *,
    config: SessionAcceptanceConfig,
) -> list[_ExternalLevel]:
    first_day = raw.index.min().normalize()
    last_day = raw.index.max().normalize()
    levels: list[_ExternalLevel] = []
    for day in pd.date_range(first_day, last_day, freq="D"):
        day = pd.Timestamp(day)
        prior_start = day - pd.Timedelta(days=1)
        prior = raw.loc[(raw.index > prior_start) & (raw.index <= day)]
        if len(prior) >= math.ceil(1440 * config.minimum_range_rows_fraction):
            high = float(prior["high"].max())
            low = float(prior["low"].min())
            levels.extend(
                [
                    _ExternalLevel(
                        level_id=f"PDH-{day.strftime('%Y%m%d')}",
                        kind="HIGH",
                        price=high,
                        source="PREVIOUS_DAY_HIGH",
                        formed_time=day - pd.Timedelta(days=1),
                        available_time=day,
                    ),
                    _ExternalLevel(
                        level_id=f"PDL-{day.strftime('%Y%m%d')}",
                        kind="LOW",
                        price=low,
                        source="PREVIOUS_DAY_LOW",
                        formed_time=day - pd.Timedelta(days=1),
                        available_time=day,
                    ),
                ]
            )
        asia_end = day + pd.Timedelta(hours=config.asia_end_hour_utc)
        asia = raw.loc[(raw.index > day) & (raw.index <= asia_end)]
        expected = config.asia_end_hour_utc * 60
        if len(asia) >= math.ceil(expected * config.minimum_range_rows_fraction):
            levels.extend(
                [
                    _ExternalLevel(
                        level_id=f"ASIA-HIGH-{day.strftime('%Y%m%d')}",
                        kind="HIGH",
                        price=float(asia["high"].max()),
                        source="COMPLETED_ASIA_HIGH",
                        formed_time=day,
                        available_time=asia_end,
                    ),
                    _ExternalLevel(
                        level_id=f"ASIA-LOW-{day.strftime('%Y%m%d')}",
                        kind="LOW",
                        price=float(asia["low"].min()),
                        source="COMPLETED_ASIA_LOW",
                        formed_time=day,
                        available_time=asia_end,
                    ),
                ]
            )
    return levels


def _untouched_at(
    level: _ExternalLevel,
    *,
    raw: pd.DataFrame,
    event_time: pd.Timestamp,
    max_age_minutes: int,
) -> bool:
    if not level.available_time < event_time:
        return False
    if event_time - level.available_time > pd.Timedelta(minutes=max_age_minutes):
        return False
    path = raw.loc[(raw.index > level.available_time) & (raw.index < event_time)]
    if path.empty:
        return True
    if level.kind == "HIGH":
        return float(path["high"].max()) < level.price
    return float(path["low"].min()) > level.price


def _select_target(
    *,
    levels: list[_ExternalLevel],
    raw: pd.DataFrame,
    event_time: pd.Timestamp,
    side: str,
    entry: float,
    max_age_minutes: int,
    excluded_level_ids: set[str],
) -> tuple[_ExternalLevel | None, int]:
    candidates = [
        level
        for level in levels
        if level.level_id not in excluded_level_ids
        and level.kind == ("HIGH" if side == "BUY" else "LOW")
        and _untouched_at(level, raw=raw, event_time=event_time, max_age_minutes=max_age_minutes)
        and (level.price > entry if side == "BUY" else level.price < entry)
    ]
    if not candidates:
        return None, 0
    if side == "BUY":
        return min(candidates, key=lambda value: value.price), len(candidates)
    return max(candidates, key=lambda value: value.price), len(candidates)


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: SessionAcceptanceConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    global _LAST_DIAGNOSTICS

    start = _normalize(evaluation_start)
    end = _normalize(evaluation_end)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")
    if raw.index.has_duplicates or not raw.index.is_monotonic_increasing:
        raise ValueError("v116 raw bars must be unique and increasing")

    ranges: dict[pd.Timestamp, _CompletedRange] = _build_completed_ranges(raw, config=config)
    levels = _build_fixed_boundary_levels(raw, config=config)
    levels.extend(
        _build_htf_swing_levels(
            raw,
            timeframe_minutes=config.target_timeframe_minutes,
            span=config.target_pivot_span_bars,
        )
    )

    tr = _true_range(raw)
    atr = tr.shift(1).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()
    body = (raw["close"] - raw["open"]).abs()
    bar_range = raw["high"] - raw["low"]
    body_threshold = body.shift(1).rolling(
        config.displacement_history_minutes,
        min_periods=config.displacement_min_history_minutes,
    ).quantile(config.displacement_body_quantile)
    range_threshold = bar_range.shift(1).rolling(
        config.displacement_history_minutes,
        min_periods=config.displacement_min_history_minutes,
    ).quantile(config.displacement_range_quantile)

    counts: Counter[str] = Counter()
    examples: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    signals: list[RotationSignal] = []
    end_position = int(raw.index.searchsorted(end, side="left"))

    def record(
        stage: str,
        *,
        side: str | None = None,
        session: str | None = None,
        event_time: pd.Timestamp | None = None,
        reason: str,
        **values: Any,
    ) -> None:
        counts[stage] += 1
        if side is not None:
            counts[f"{stage}:{side}"] += 1
        if session is not None:
            counts[f"{stage}:{session}"] += 1
        if len(examples[stage]) < 7:
            examples[stage].append(
                {
                    "side": side,
                    "session": session,
                    "event_time_utc": event_time.isoformat() if event_time is not None else None,
                    "reason": reason,
                    **values,
                }
            )

    for completed_range in ranges.values():
        record(
            "DEALING_RANGE_COMPLETED",
            event_time=completed_range.available_time,
            reason="source range fully closed and its boundaries became immutable",
            range_id=completed_range.range_id,
            source=completed_range.source,
            high=completed_range.high,
            low=completed_range.low,
        )

    for index in range(1, len(raw)):
        timestamp = raw.index[index]
        if not start <= timestamp < end:
            continue
        completed_range = ranges.get(timestamp.normalize())
        if completed_range is None or not completed_range.available_time < timestamp < completed_range.valid_end:
            continue
        atr_value = float(atr.iloc[index])
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue

        current = raw.iloc[index]
        previous_close = float(raw.iloc[index - 1]["close"])
        high_crossed = (
            not completed_range.high_consumed
            and previous_close <= completed_range.high + config.reacceptance_buffer_atr * atr_value
            and float(current["high"]) >= completed_range.high + config.sweep_min_atr * atr_value
        )
        low_crossed = (
            not completed_range.low_consumed
            and previous_close >= completed_range.low - config.reacceptance_buffer_atr * atr_value
            and float(current["low"]) <= completed_range.low - config.sweep_min_atr * atr_value
        )
        if not high_crossed and not low_crossed:
            continue
        if high_crossed:
            completed_range.high_consumed = True
        if low_crossed:
            completed_range.low_consumed = True
        session = _session_label(timestamp)
        if high_crossed and low_crossed:
            record(
                "AMBIGUOUS_TWO_SIDED_RANGE_BREAK",
                session=session,
                event_time=timestamp,
                reason="one completed minute consumed both boundaries; intrabar ordering is unknowable",
                range_id=completed_range.range_id,
            )
            continue

        side = "BUY" if high_crossed else "SELL"
        boundary = completed_range.high if high_crossed else completed_range.low
        breakout_extreme = float(current["high"] if high_crossed else current["low"])
        first_close_accepted = (
            float(current["close"]) > boundary + config.acceptance_buffer_atr * atr_value
            if side == "BUY"
            else float(current["close"]) < boundary - config.acceptance_buffer_atr * atr_value
        )
        if not first_close_accepted:
            record(
                "BOUNDARY_CONSUMED_WITHOUT_ACCEPTANCE",
                side=side,
                session=session,
                event_time=timestamp,
                reason="first boundary break did not close beyond the immutable range",
                range_id=completed_range.range_id,
                boundary=boundary,
            )
            continue

        record(
            "INITIAL_RANGE_BREAK_ACCEPTED",
            side=side,
            session=session,
            event_time=timestamp,
            reason="first boundary break closed outside the completed range",
            range_id=completed_range.range_id,
            boundary=boundary,
            breakout_extreme=breakout_extreme,
        )

        fvg: dict[str, Any] | None = None
        acceptance_time: pd.Timestamp | None = None
        consecutive = 1
        displacement_seen = False
        body_limit = float(body_threshold.iloc[index])
        range_limit = float(range_threshold.iloc[index])
        if math.isfinite(body_limit) and math.isfinite(range_limit):
            displacement_seen = (
                float(body.iloc[index]) >= body_limit
                and float(bar_range.iloc[index]) >= range_limit
                and (
                    float(current["close"]) > float(current["open"])
                    if side == "BUY"
                    else float(current["close"]) < float(current["open"])
                )
            )
        scan_end = min(
            len(raw) - 1,
            end_position - 1,
            index + config.acceptance_window_minutes,
        )
        for third_index in range(index + 1, scan_end + 1):
            bar = raw.iloc[third_index]
            accepted = (
                float(bar["close"]) > boundary + config.acceptance_buffer_atr * atr_value
                if side == "BUY"
                else float(bar["close"]) < boundary - config.acceptance_buffer_atr * atr_value
            )
            if accepted:
                consecutive += 1
            else:
                consecutive = 0
                displacement_seen = False
            body_limit = float(body_threshold.iloc[third_index])
            range_limit = float(range_threshold.iloc[third_index])
            if accepted and math.isfinite(body_limit) and math.isfinite(range_limit):
                directional = (
                    float(bar["close"]) > float(bar["open"])
                    if side == "BUY"
                    else float(bar["close"]) < float(bar["open"])
                )
                displacement_seen = displacement_seen or (
                    directional
                    and float(body.iloc[third_index]) >= body_limit
                    and float(bar_range.iloc[third_index]) >= range_limit
                )
            if consecutive < config.acceptance_closes or not displacement_seen or third_index < index + 1:
                continue
            bos = (
                float(bar["close"]) > breakout_extreme + config.bos_beyond_breakout_atr * atr_value
                if side == "BUY"
                else float(bar["close"]) < breakout_extreme - config.bos_beyond_breakout_atr * atr_value
            )
            if not bos:
                continue
            candidate = _directional_fvg(
                raw=raw,
                third_index=third_index,
                side=side,
                body=body,
                bar_range=bar_range,
                body_threshold=body_threshold,
                range_threshold=range_threshold,
                atr_value=atr_value,
                minimum_gap_atr=config.fvg_min_atr,
            )
            if candidate is None:
                continue
            midpoint = float((candidate["gap_lower"] + candidate["gap_upper"]) / 2.0)
            held_outside = midpoint > boundary if side == "BUY" else midpoint < boundary
            if held_outside:
                fvg = candidate
                acceptance_time = raw.index[third_index]
                break

        if fvg is None or acceptance_time is None:
            record(
                "NO_ACCEPTANCE_DISPLACEMENT_BOS_FVG",
                side=side,
                session=session,
                event_time=timestamp,
                reason="outside closes did not complete sustained acceptance, displacement, BOS and an outside FVG",
                range_id=completed_range.range_id,
                required_acceptance_closes=config.acceptance_closes,
            )
            continue

        record(
            "ACCEPTANCE_DISPLACEMENT_BOS_FVG_CONFIRMED",
            side=side,
            session=session,
            event_time=timestamp,
            reason="completed bars accepted price outside the range and left an initiative imbalance",
            range_id=completed_range.range_id,
            acceptance_time_utc=acceptance_time.isoformat(),
            fvg_size_atr=float(fvg["gap_size"]) / atr_value,
        )

        retrace_level = float(
            fvg["gap_lower"]
            + config.fvg_retrace_fraction * (fvg["gap_upper"] - fvg["gap_lower"])
        )
        retest_index: int | None = None
        invalidation_reason: str | None = None
        retest_start = int(fvg["third_index"]) + 1
        retest_end = min(
            len(raw) - 2,
            end_position - 2,
            retest_start + config.retrace_window_minutes - 1,
        )
        for candidate_index in range(retest_start, retest_end + 1):
            candidate = raw.iloc[candidate_index]
            if side == "BUY":
                if float(candidate["close"]) < boundary - config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "accepted upside discovery closed back inside the completed range"
                    break
                touched = float(candidate["low"]) <= retrace_level
                rejected = (
                    float(candidate["close"]) > retrace_level
                    and float(candidate["close"]) > float(candidate["open"])
                    and float(candidate["close"]) > boundary
                )
            else:
                if float(candidate["close"]) > boundary + config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "accepted downside discovery closed back inside the completed range"
                    break
                touched = float(candidate["high"]) >= retrace_level
                rejected = (
                    float(candidate["close"]) < retrace_level
                    and float(candidate["close"]) < float(candidate["open"])
                    and float(candidate["close"]) < boundary
                )
            if touched and rejected:
                retest_index = candidate_index
                break

        if retest_index is None:
            record(
                "ACCEPTANCE_FAILED" if invalidation_reason else "NO_OUTSIDE_FVG_RETEST",
                side=side,
                session=session,
                event_time=timestamp,
                reason=invalidation_reason or "accepted outside FVG was not retested and held within its state lifetime",
                range_id=completed_range.range_id,
                retrace_level=retrace_level,
            )
            continue

        activation_index = retest_index + config.activation_delay_minutes
        activation_time = raw.index[activation_index]
        if not start <= activation_time < end:
            continue
        entry = float(raw.iloc[activation_index]["close"])
        excluded = {
            f"ASIA-HIGH-{timestamp.normalize().strftime('%Y%m%d')}",
            f"ASIA-LOW-{timestamp.normalize().strftime('%Y%m%d')}",
        }
        target_level, target_candidates = _select_target(
            levels=levels,
            raw=raw,
            event_time=timestamp,
            side=side,
            entry=entry,
            max_age_minutes=config.target_level_max_age_minutes,
            excluded_level_ids=excluded,
        )
        if target_level is None:
            record(
                "NO_PREOBSERVED_EXTERNAL_OBJECTIVE",
                side=side,
                session=session,
                event_time=timestamp,
                reason="no untouched completed-range or confirmed HTF swing liquidity remained beyond entry",
                range_id=completed_range.range_id,
                entry=entry,
            )
            continue
        target = target_level.price
        stop = (
            boundary - config.stop_buffer_atr * atr_value
            if side == "BUY"
            else boundary + config.stop_buffer_atr * atr_value
        )
        geometry = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry:
            record(
                "ENTRY_GEOMETRY_REJECTED",
                side=side,
                session=session,
                event_time=timestamp,
                reason="entry was no longer between failed-acceptance invalidation and pre-observed external liquidity",
                range_id=completed_range.range_id,
                entry=entry,
                stop=stop,
                target=target,
            )
            continue

        rr = cost_after_reward_risk(
            entry=entry,
            stop=stop,
            target=target,
            side=side,
            costs=costs,
        )
        if not math.isfinite(rr) or not (
            config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr
        ):
            record(
                "COST_AFTER_RR_REJECTED",
                side=side,
                session=session,
                event_time=timestamp,
                reason="nearest pre-observed external liquidity could not pay realistic entry/failure costs",
                range_id=completed_range.range_id,
                cost_after_reward_risk=rr,
                target_source=target_level.source,
            )
            continue

        retest_time = raw.index[retest_index]
        feature_time, feature_details = _latest_feature(state, retest_time)
        activation_ns = int(activation_time.value)
        details: dict[str, Any] = {
            "scenario": "COMPLETED_SESSION_RANGE_ACCEPTANCE_CONTINUATION",
            "liquidity_source": completed_range.source,
            "state_sequence": [
                "DEALING_RANGE_COMPLETED",
                "BOUNDARY_CONSUMED",
                "OUTSIDE_CLOSE_ACCEPTED",
                "DISPLACEMENT_AND_BOS",
                "OUTSIDE_FVG_FORMED",
                "OUTSIDE_FVG_RETEST_HELD",
                "ENTRY_ARMED",
            ],
            "range_id": completed_range.range_id,
            "range_available_time_ns": int(completed_range.available_time.value),
            "range_high": completed_range.high,
            "range_low": completed_range.low,
            "range_width": completed_range.width,
            "breakout_time_ns": int(timestamp.value),
            "consumed_boundary": boundary,
            "breakout_extreme": breakout_extreme,
            "acceptance_time_ns": int(acceptance_time.value),
            "required_acceptance_closes": config.acceptance_closes,
            "displacement_time_ns": int(fvg["displacement_time"].value),
            "fvg_time_ns": int(fvg["fvg_time"].value),
            "fvg_lower": fvg["gap_lower"],
            "fvg_upper": fvg["gap_upper"],
            "fvg_retrace_level": retrace_level,
            "fvg_retest_time_ns": int(retest_time.value),
            "activation_time_ns": activation_ns,
            "failed_acceptance_invalidation": stop,
            "target_level": target_level.as_dict(),
            "target_candidate_count": target_candidates,
            "session": session,
            "atr_1m_prior_to_breakout": atr_value,
            "latest_available_feature_open_time_ns": (
                int(feature_time.value) if feature_time is not None else None
            ),
            "microstructure_context": feature_details,
        }
        score = (
            rr
            + float(fvg["body_ratio"])
            + float(fvg["range_ratio"])
            + float(fvg["gap_size"]) / atr_value
        )
        signals.append(
            RotationSignal(
                scenario_id=(
                    f"v116-{completed_range.source.lower()}-acceptance-{side.lower()}-{activation_ns}"
                ),
                observed_time_ns=activation_ns,
                side=side,
                entry_reference=entry,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=rr,
                score=score,
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=(
                    int(feature_time.value)
                    if feature_time is not None
                    else int(timestamp.value)
                ),
                source_feature_available_time_ns=activation_ns,
                source_max_market_time_ns=int(retest_time.value),
                details=details,
            )
        )
        record(
            "ENTRY_ARMED",
            side=side,
            session=session,
            event_time=timestamp,
            reason="completed range acceptance, initiative transfer, outside retest and pre-observed target aligned",
            range_id=completed_range.range_id,
            activation_time_utc=activation_time.isoformat(),
            target_source=target_level.source,
            cost_after_reward_risk=rr,
        )

    by_activation: dict[int, RotationSignal] = {}
    for signal in signals:
        prior = by_activation.get(signal.observed_time_ns)
        if prior is None or signal.score > prior.score:
            by_activation[signal.observed_time_ns] = signal
    result = sorted(by_activation.values(), key=lambda value: value.observed_time_ns)
    for signal in result:
        if signal.source_max_market_time_ns >= signal.observed_time_ns:
            raise AssertionError("v116 entry used an incomplete activation bar")
        if signal.observed_time_ns - signal.source_max_market_time_ns != 60_000_000_000:
            raise AssertionError("v116 activation delay is not one completed minute")
        if signal.source_feature_available_time_ns != signal.observed_time_ns:
            raise AssertionError("v116 feature availability contract mismatch")
        if int(signal.details["range_available_time_ns"]) >= int(signal.details["breakout_time_ns"]):
            raise AssertionError("v116 range was not completed before breakout")
        if int(signal.details["target_level"]["available_time_ns"]) >= int(signal.details["breakout_time_ns"]):
            raise AssertionError("v116 target was not available before breakout")

    _LAST_DIAGNOSTICS = {
        "summary": {
            "candidate": "candidate-02-v116-session-acceptance-continuation",
            "liquidity_source": config.liquidity_source,
            "required_acceptance_closes": config.acceptance_closes,
            "detector": "first break of an immutable completed session boundary",
            "scenario": "outside acceptance -> displacement/BOS/FVG -> outside retest -> pre-observed external liquidity",
            "external_levels_built": len(levels),
            "completed_ranges_available": len(ranges),
            "counts": dict(sorted(counts.items())),
            "signals_emitted": len(result),
            "future_information_used": False,
        },
        "examples": dict(examples),
    }
    return result
