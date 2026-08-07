#!/usr/bin/env python3
"""Candidate-04 V53: common-factor forced-deleveraging continuation.

This is a causal intent compiler only. NautilusTrader remains the sole owner of
orders, fills, fees, positions, risk, PnL and NAV.

The candidate tests a market cause distinct from V52's failed new-inventory
route. A pre-existing BTC external-liquidity edge is swept and accepted by the
robust four-asset return/order-flow factor while raw exchange open interest is
destroyed. Price continuing in the sweep direction despite material OI
contraction identifies forced deleveraging rather than fresh inventory. A later
FVG/old-boundary retest must hold, the common factor must resume and most of the
OI contraction must remain unrepaired before continuation is emitted toward
pre-existing external liquidity.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

import common_factor_accepted_auction_compiler as v52
import cross_market_information_transfer_compiler as base
import cross_market_information_transfer_compiler_v2 as v2
import cross_market_smt_liquidity_reversal_compiler as shared

SYMBOLS = base.SYMBOLS
SCENARIO = "COMMON_FACTOR_FORCED_DELEVERAGING_CONTINUATION"
THRESHOLD_WINDOW = 720
THRESHOLD_MIN = 240
RANGE_MINUTES = 60
RETEST_BARS = 12
COOLDOWN_BARS = 20
MIN_BREADTH = 3
MIN_COMMON_RETURN = 0.35
MIN_COMMON_FLOW = 0.20
MAX_OI_REBUILD_SHARE = 0.20


@dataclass(frozen=True, slots=True)
class Candidate:
    event_index: int
    signal_index: int
    side: int
    stop_level: float
    priority: float
    details: dict[str, Any]


def shifted_contraction_median(series: pd.Series) -> pd.Series:
    """Past-only median magnitude of negative observed OI changes."""
    raw = series.astype(float)
    contraction = (-raw).where(raw < 0.0)
    return (
        contraction.shift(1)
        .rolling(THRESHOLD_WINDOW, min_periods=THRESHOLD_MIN)
        .median()
    )


def state_oi_contraction(
    frame: pd.DataFrame,
    event_index: int,
    retest_index: int,
    cutoff: float,
    *,
    maximum_rebuild_share: float = MAX_OI_REBUILD_SHARE,
    require_contraction: bool = True,
) -> tuple[bool, dict[str, float]]:
    """Measure causal OI destruction and retained contraction over the state.

    The baseline requires a material trough relative to the completed minute
    immediately before the sweep and requires at least 80% of that contraction
    to remain at the retest. The diagnostic ablation disables only this economic
    mechanism while preserving every price, common-factor and execution state.
    """
    if not (0 < event_index < retest_index < len(frame)):
        return False, {}
    values = frame["metric_sum_open_interest"].astype(float)
    pre = float(values.iloc[event_index - 1])
    segment = values.iloc[event_index : retest_index + 1]
    if segment.empty:
        return False, {}
    trough = float(segment.min())
    end = float(segment.iloc[-1])
    if not all(math.isfinite(value) and value > 0.0 for value in (pre, trough, end)):
        return False, {}
    contraction = max(1.0 - trough / pre, 0.0)
    retained = max(1.0 - end / pre, 0.0)
    rebuild_share = (
        max((end - trough) / max(pre - trough, 1e-12), 0.0)
        if contraction > 0.0
        else 1.0
    )
    cutoff_valid = math.isfinite(cutoff) and cutoff > 0.0
    passed = bool(
        not require_contraction
        or (
            cutoff_valid
            and contraction >= cutoff
            and retained >= (1.0 - maximum_rebuild_share) * contraction
            and rebuild_share <= maximum_rebuild_share
        )
    )
    return passed, {
        "pre_event_open_interest": pre,
        "state_trough_open_interest": trough,
        "retest_open_interest": end,
        "state_oi_contraction": contraction,
        "retained_oi_contraction": retained,
        "oi_rebuild_share": rebuild_share,
        "past_only_oi_contraction_cutoff": cutoff,
        "maximum_oi_rebuild_share": maximum_rebuild_share,
        "oi_contraction_required": float(require_contraction),
    }


def collect_candidates(
    frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    stop_buffer_atr: float,
    *,
    minimum_breadth: int = MIN_BREADTH,
    minimum_common_return: float = MIN_COMMON_RETURN,
    minimum_common_flow: float = MIN_COMMON_FLOW,
    require_oi_contraction: bool = True,
) -> tuple[list[Candidate], dict[str, Any]]:
    btc = frames["BTCUSDT"]
    factors = v52.normalized_factor_state(frames)
    upper, lower = shared.causal_range_edges(btc)
    body_atr = (
        (btc["close"].astype(float) - btc["open"].astype(float)).abs()
        / btc["atr"].astype(float)
    )
    body_cutoff = v52.shifted_quantile(body_atr, 0.55)
    contraction_cutoff = shifted_contraction_median(
        btc["metric_oi_change_15m"]
    )

    counts: dict[str, Any] = {
        "btc_first_external_sweeps": 0,
        "common_factor_acceptance": 0,
        "outside_acceptance": 0,
        "displacement_fvg": 0,
        "retests": 0,
        "oi_contraction": 0,
        "qualified": 0,
        "cooldown_suppressed": 0,
        "minimum_breadth": minimum_breadth,
        "minimum_common_return": minimum_common_return,
        "minimum_common_flow": minimum_common_flow,
        "require_oi_contraction": require_oi_contraction,
    }
    output: list[Candidate] = []
    last_signal = -10**9

    for event_index, timestamp in enumerate(btc.index):
        if timestamp < evaluation_start or timestamp >= evaluation_end:
            continue
        if event_index < RANGE_MINUTES or event_index >= len(btc) - 2:
            continue
        row = btc.iloc[event_index]
        previous = btc.iloc[event_index - 1]
        atr = float(row["atr"])
        upper_edge = float(upper.iloc[event_index])
        lower_edge = float(lower.iloc[event_index])
        if not all(math.isfinite(value) for value in (atr, upper_edge, lower_edge)) or atr <= 0.0:
            continue
        high_sweep = (
            float(previous["close"]) <= upper_edge
            and float(row["high"]) >= upper_edge + 0.02 * atr
        )
        low_sweep = (
            float(previous["close"]) >= lower_edge
            and float(row["low"]) <= lower_edge - 0.02 * atr
        )
        if high_sweep == low_sweep:
            continue
        side = 1 if high_sweep else -1
        boundary = upper_edge if side > 0 else lower_edge
        sweep_extreme = float(row["high"] if side > 0 else row["low"])
        counts["btc_first_external_sweeps"] += 1

        factor_pass, factor_details = v52.common_factor_acceptance(
            factors,
            event_index,
            side,
            minimum_breadth=minimum_breadth,
            minimum_common_return=minimum_common_return,
            minimum_common_flow=minimum_common_flow,
        )
        if not factor_pass:
            continue
        counts["common_factor_acceptance"] += 1

        classification_end, acceptance_details = v52.classify_outside_acceptance(
            btc,
            event_index,
            boundary,
            side,
        )
        if classification_end is None:
            continue
        counts["outside_acceptance"] += 1

        displacement = v52.find_displacement(
            btc,
            event_index,
            classification_end,
            boundary,
            side,
            body_cutoff,
        )
        if displacement is None:
            continue
        displacement_index, fvg, displacement_details = displacement
        counts["displacement_fvg"] += 1

        signal_index: int | None = None
        retest_details: dict[str, float] = {}
        oi_details: dict[str, float] = {}
        last = min(displacement_index + RETEST_BARS, len(btc) - 2)
        for index in range(displacement_index + 1, last + 1):
            current = btc.iloc[index]
            current_atr = float(current["atr"])
            if not math.isfinite(current_atr) or current_atr <= 0.0:
                continue
            invalidated = (
                float(current["close"]) <= boundary - 0.10 * current_atr
                if side > 0
                else float(current["close"]) >= boundary + 0.10 * current_atr
            )
            if invalidated:
                break
            held, details = v52.retest_holds(
                btc,
                factors,
                index,
                side,
                boundary,
                fvg,
                max(minimum_common_return * 0.50, 0.0),
            )
            if not held:
                continue
            counts["retests"] += 1
            oi_pass, state_details = state_oi_contraction(
                btc,
                event_index,
                index,
                float(contraction_cutoff.iloc[event_index]),
                require_contraction=require_oi_contraction,
            )
            if not oi_pass:
                continue
            counts["oi_contraction"] += 1
            signal_index = index
            retest_details = details
            oi_details = state_details
            break
        if signal_index is None:
            continue
        if signal_index - last_signal <= COOLDOWN_BARS:
            counts["cooldown_suppressed"] += 1
            continue

        signal_row = btc.iloc[signal_index]
        signal_atr = float(signal_row["atr"])
        retest_extreme = float(
            signal_row["low"] if side > 0 else signal_row["high"]
        )
        old_range_invalidation = boundary - side * 0.10 * signal_atr
        stop = (
            min(
                retest_extreme - stop_buffer_atr * signal_atr,
                old_range_invalidation,
            )
            if side > 0
            else max(
                retest_extreme + stop_buffer_atr * signal_atr,
                old_range_invalidation,
            )
        )
        entry = float(signal_row["close"])
        if not math.isfinite(stop) or side * (entry - stop) <= 0.0:
            continue
        priority = (
            float(factor_details["common_directional_return_factor"])
            * float(factor_details["common_directional_flow_factor"])
            * max(float(oi_details.get("state_oi_contraction", 0.0)), 1e-6)
            * max(float(signal_row["notional_60s"]), 1.0)
        )
        fvg_low, fvg_high = fvg
        details: dict[str, Any] = {
            "compiler": "candidate-04-v53-common-factor-deleveraging-v1",
            "market_cause": (
                "the common crypto market accepted a BTC external-liquidity "
                "sweep while raw open interest contracted materially, so price "
                "delivery was driven by forced position destruction rather than "
                "new inventory; a separate retest held without OI repair"
            ),
            "state_sequence": [
                "SHIFTED_60M_EXTERNAL_LIQUIDITY",
                "BTC_FIRST_EXTERNAL_SWEEP",
                "COMMON_RETURN_AND_ORDER_FLOW_FACTOR_ACCEPTANCE",
                "TWO_OF_THREE_OUTSIDE_CLOSES",
                "DIRECTIONAL_DISPLACEMENT_FVG",
                "SEPARATE_FVG_AND_OLD_BOUNDARY_RETEST",
                "MATERIAL_STATE_INTERVAL_OI_CONTRACTION",
                "NO_MATERIAL_OI_REBUILD",
                "COMMON_FACTOR_RESUMPTION",
            ],
            "event_time": timestamp.isoformat(),
            "sweep_direction": side,
            "trade_direction": side,
            "external_boundary": boundary,
            "sweep_extreme": sweep_extreme,
            "event_extension_atr": side * (sweep_extreme - boundary) / atr,
            "classification_index": classification_end,
            "displacement_index": displacement_index,
            "signal_index": signal_index,
            "fvg_low": fvg_low,
            "fvg_high": fvg_high,
            "fvg_midpoint": 0.5 * (fvg_low + fvg_high),
            "old_range_invalidation": old_range_invalidation,
            "structural_stop": stop,
            "minimum_target_net_r": 1.20,
            "risk_multiplier": 1.0,
            **factor_details,
            **acceptance_details,
            **displacement_details,
            **retest_details,
            **oi_details,
        }
        output.append(
            Candidate(
                event_index=event_index,
                signal_index=signal_index,
                side=side,
                stop_level=stop,
                priority=priority,
                details=details,
            )
        )
        counts["qualified"] += 1
        last_signal = signal_index
    return output, counts


def write_outputs(
    output: Path,
    candidates: list[Candidate],
    counts: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    nt_frames: dict[str, pd.DataFrame],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    rows_by_symbol: dict[str, list[dict[str, Any]]] = {
        symbol: [] for symbol in SYMBOLS
    }
    for item in candidates:
        timestamp = frames["BTCUSDT"].index[item.signal_index]
        if not evaluation_start <= timestamp < evaluation_end:
            continue
        observe_time = nt_frames["BTCUSDT"].index[item.signal_index]
        rows_by_symbol["BTCUSDT"].append(
            {
                "scenario": SCENARIO,
                "side": item.side,
                "signal_index": item.signal_index,
                "signal_time": timestamp.isoformat(),
                "observe_time": observe_time.isoformat(),
                "observe_time_ns": int(observe_time.value),
                "stop_level": item.stop_level,
                "event_indices": [item.event_index, item.signal_index],
                "details": item.details,
            }
        )
    for symbol, rows in rows_by_symbol.items():
        target = output / symbol
        target.mkdir(parents=True, exist_ok=True)
        (target / "signals.json").write_text(
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (target / "summary.json").write_text(
            json.dumps(
                {"symbol": symbol, "written_signals": len(rows)},
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
    summary = {
        "candidate": "candidate-04-v53-common-factor-forced-deleveraging-continuation",
        "compiler": "candidate-04-v53-common-factor-deleveraging-v1",
        "written_signals": sum(len(rows) for rows in rows_by_symbol.values()),
        "signals_by_symbol": {
            symbol: len(rows) for symbol, rows in rows_by_symbol.items()
        },
        "route_counts": counts,
        "scenario_contract": {
            "common_factor": (
                "past-only robust cross-sectional return and executed-flow "
                "acceptance, not a fixed leader-lagger coefficient"
            ),
            "deleveraging": (
                "raw OI contraction from before the sweep to the state trough, "
                "with at least 80% of the contraction retained at the retest"
            ),
            "entry": "separate FVG/old-boundary retest plus factor resumption",
            "invalidation": "retest extreme or causal return inside old range",
            "target": "nearest pre-existing intact external liquidity; no measured move",
        },
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rich-root", required=True, type=Path)
    parser.add_argument("--config-root", required=True, type=Path)
    parser.add_argument("--kline-root", required=True, type=Path)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.05)
    parser.add_argument("--minimum-breadth", type=int, default=MIN_BREADTH)
    parser.add_argument("--minimum-common-return", type=float, default=MIN_COMMON_RETURN)
    parser.add_argument("--minimum-common-flow", type=float, default=MIN_COMMON_FLOW)
    parser.add_argument("--disable-oi-contraction", action="store_true")
    args = parser.parse_args()
    evaluation_start = pd.Timestamp(args.evaluation_start, tz="UTC")
    evaluation_end = pd.Timestamp(args.evaluation_end, tz="UTC")
    frames, nt_frames = v2.load_frames(
        args.rich_root,
        args.config_root,
        args.kline_root,
        evaluation_start,
        evaluation_end,
    )
    candidates, counts = collect_candidates(
        frames,
        evaluation_start,
        evaluation_end,
        args.stop_buffer_atr,
        minimum_breadth=args.minimum_breadth,
        minimum_common_return=args.minimum_common_return,
        minimum_common_flow=args.minimum_common_flow,
        require_oi_contraction=not args.disable_oi_contraction,
    )
    write_outputs(
        args.output,
        candidates,
        counts,
        frames,
        nt_frames,
        evaluation_start,
        evaluation_end,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
