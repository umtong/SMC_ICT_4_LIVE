#!/usr/bin/env python3
"""Cross-sectional session raid with the first still-active objective.

The source PDFs do not instruct the trader to ignore nearer opposing structure
and always hold to the far side of an entire session range. They name the first
opposing high/low, OB, FVG, trendline or channel boundary as a profit objective.
A human also does not treat an old micro pivot that price has crossed many times
as current opposing liquidity. Earlier v13 did exactly that and could reject a
trade because the confirmation bar had crossed a stale level.

This implementation therefore:

* builds causal directional-change pivots;
* retires every pivot consumed after confirmation and before the setup;
* retires pivots consumed by the setup/reclaim bar itself;
* selects the nearest remaining active objective;
* rejects only when that active first objective offers less than 1R;
* retains the far structural cap when no active internal pivot exists.

Every decision is recorded in ``LAST_TARGET_AUDIT_ROWS`` so zero opportunity or
rejection can be traced to concrete levels and bars rather than an aggregate
gate.
"""
from __future__ import annotations

from dataclasses import asdict
import os

import pandas as pd

from data import resample
from domain_v3 import Side
from market_v5 import DirectionalChangePivotDetector
from market_v13 import pivot_key, select_first_directional_objective
import screen_v10 as _base


_ORIGINAL_ROUTE = _base.route_setups
TARGET_MINUTES = int(os.environ.get("EC_TARGET_MINUTES", "5"))
TARGET_DC_ATR = float(os.environ.get("EC_TARGET_DC_ATR", "1.0"))
LAST_TARGET_AUDIT_ROWS: list[dict[str, object]] = []


def _target_frame(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    return frame if minutes == 5 else resample(frame, minutes)


def _pivots(frame: pd.DataFrame, minutes: int):
    working = _target_frame(frame, minutes)
    candles = _base.to_candles(working)
    detector = DirectionalChangePivotDetector(
        timeframe_minutes=minutes,
        atr_period=14,
        atr_multiple=TARGET_DC_ATR,
    )
    output = []
    for index, candle in enumerate(candles):
        pivot = detector.on_candle(candle, index)
        if pivot is not None:
            output.append(pivot)
    return output


def _setup_bar(frame: pd.DataFrame, observed_time_ns: int):
    observed = pd.Timestamp(int(observed_time_ns), unit="ns", tz="UTC")
    selected = frame[frame["close_time_dt"] == observed]
    return None if selected.empty else selected.iloc[-1]


def _pivot_rows(pivots) -> list[dict[str, object]]:
    return [
        {
            "side": pivot.side,
            "level": float(pivot.level),
            "event_time_ns": int(pivot.event_time_ns),
            "observed_time_ns": int(pivot.observed_time_ns),
        }
        for pivot in pivots
    ]


def _historically_consumed_pivot_keys(
    *,
    setup,
    pivots,
    frame: pd.DataFrame,
):
    setup_observed = pd.Timestamp(
        int(setup.observed_time_ns),
        unit="ns",
        tz="UTC",
    )
    consumed = set()
    for pivot in pivots:
        pivot_observed = pd.Timestamp(
            int(pivot.observed_time_ns),
            unit="ns",
            tz="UTC",
        )
        selected = frame[
            (frame["close_time_dt"] > pivot_observed)
            & (frame["close_time_dt"] < setup_observed)
        ]
        if selected.empty:
            continue
        if pivot.side == "HIGH":
            was_consumed = bool((selected["high"] >= pivot.level).any())
        else:
            was_consumed = bool((selected["low"] <= pivot.level).any())
        if was_consumed:
            consumed.add(pivot_key(pivot))
    return consumed


def route_setups(*args, **kwargs):
    routed, diagnostics = _ORIGINAL_ROUTE(*args, **kwargs)
    five_frames = kwargs["five_frames"]
    target_frames = {
        symbol: _target_frame(frame, TARGET_MINUTES)
        for symbol, frame in five_frames.items()
    }
    pivots_by_symbol = {
        symbol: _pivots(frame, TARGET_MINUTES)
        for symbol, frame in five_frames.items()
    }
    output = []
    LAST_TARGET_AUDIT_ROWS.clear()

    def count(key: str, amount: int = 1) -> None:
        diagnostics[key] = diagnostics.get(key, 0) + amount

    for setup in routed:
        row = _setup_bar(target_frames[setup.symbol], setup.observed_time_ns)
        audit: dict[str, object] = {
            "setup_id": setup.setup_id,
            "causal_event_id": setup.causal_event_id,
            "symbol": setup.symbol,
            "family": setup.family,
            "side": int(setup.side),
            "observed_time_ns": int(setup.observed_time_ns),
            "source_pool_id": setup.source_pool_id,
            "entry": float(setup.entry),
            "stop": float(setup.stop),
            "far_target": float(setup.initial_target),
            "far_gross_rr": float(
                abs(setup.initial_target - setup.entry)
                / abs(setup.entry - setup.stop)
            ),
            "target_timeframe_minutes": TARGET_MINUTES,
            "target_dc_atr": TARGET_DC_ATR,
        }
        if row is None:
            audit["disposition"] = "REJECT_MISSING_SETUP_BAR"
            LAST_TARGET_AUDIT_ROWS.append(audit)
            count("missing_setup_bar")
            continue
        audit.update(
            {
                "setup_bar_open": float(row.open),
                "setup_bar_high": float(row.high),
                "setup_bar_low": float(row.low),
                "setup_bar_close": float(row.close),
            },
        )

        pivots = [
            pivot
            for pivot in pivots_by_symbol[setup.symbol]
            if pivot.observed_time_ns < setup.observed_time_ns
        ]
        if setup.side is Side.LONG:
            geometric_candidates = [
                pivot
                for pivot in pivots
                if pivot.side == "HIGH"
                and setup.entry < pivot.level <= setup.initial_target
            ]
        else:
            geometric_candidates = [
                pivot
                for pivot in pivots
                if pivot.side == "LOW"
                and setup.initial_target <= pivot.level < setup.entry
            ]
        historical_consumed = _historically_consumed_pivot_keys(
            setup=setup,
            pivots=geometric_candidates,
            frame=target_frames[setup.symbol],
        )
        decision = select_first_directional_objective(
            setup=setup,
            pivots=pivots,
            setup_bar_high=float(row.high),
            setup_bar_low=float(row.low),
            timeframe_minutes=TARGET_MINUTES,
            consumed_pivot_keys=historical_consumed,
        )
        excluded_keys = {pivot_key(pivot) for pivot in decision.excluded_consumed}
        setup_bar_consumed = [
            pivot
            for pivot in decision.excluded_consumed
            if pivot_key(pivot) not in historical_consumed
        ]
        audit.update(
            {
                "eligible_pivots": _pivot_rows(pivots),
                "candidate_pivots": _pivot_rows(decision.candidates),
                "historically_consumed_pivots": _pivot_rows(
                    [
                        pivot
                        for pivot in decision.candidates
                        if pivot_key(pivot) in historical_consumed
                    ],
                ),
                "setup_bar_consumed_pivots": _pivot_rows(setup_bar_consumed),
                "active_candidate_pivots": _pivot_rows(
                    [
                        pivot
                        for pivot in decision.candidates
                        if pivot_key(pivot) not in excluded_keys
                    ],
                ),
                "chosen_pivot": (
                    None if decision.pivot is None else asdict(decision.pivot)
                ),
            },
        )
        count("historically_consumed_objectives_excluded", len(historical_consumed))
        count("setup_bar_consumed_objectives_excluded", len(setup_bar_consumed))

        if decision.reason == "NO_ACTIVE_INTERNAL_OBJECTIVE_USE_FAR_CAP":
            audit["disposition"] = "FAR_BOUNDARY_FALLBACK_NO_ACTIVE_INTERNAL_OBJECTIVE"
            LAST_TARGET_AUDIT_ROWS.append(audit)
            count("no_active_internal_objective_fallback_far_boundary")
            assert decision.setup is not None
            output.append(decision.setup)
            continue

        if decision.reason == "FIRST_ACTIVE_OBJECTIVE_RR_LT_1":
            assert decision.pivot is not None
            audit["disposition"] = "REJECT_FIRST_ACTIVE_OBJECTIVE_RR_LT_1"
            audit["selected_target"] = float(decision.pivot.level)
            audit["selected_gross_rr"] = float(
                abs(decision.pivot.level - setup.entry)
                / abs(setup.entry - setup.stop)
            )
            LAST_TARGET_AUDIT_ROWS.append(audit)
            count("first_active_structural_objective_rr_lt_1")
            continue

        if decision.reason != "FIRST_ACTIVE_OBJECTIVE_SELECTED":
            raise RuntimeError(f"unexpected first-objective decision: {decision.reason}")
        assert decision.setup is not None and decision.pivot is not None
        plan = decision.setup.executable(
            decision.setup.initial_target,
            target_id=decision.setup.fixed_target_id,
            min_gross_rr=1.0,
        )
        assert plan is not None
        audit["disposition"] = "SELECT_FIRST_ACTIVE_OBJECTIVE"
        audit["selected_target"] = float(decision.pivot.level)
        audit["selected_gross_rr"] = float(plan.gross_rr)
        LAST_TARGET_AUDIT_ROWS.append(audit)
        output.append(decision.setup)
        count("first_active_structural_objective_selected")

    output.sort(key=lambda item: (item.observed_time_ns, item.symbol, item.setup_id))
    return output, diagnostics


_base.route_setups = route_setups


if __name__ == "__main__":
    _base.main()
