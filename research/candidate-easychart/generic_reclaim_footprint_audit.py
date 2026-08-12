#!/usr/bin/env python3
"""Audit every generic delayed reclaim before deciding what actually failed.

The v10 development week contains fourteen routed setups and eleven completed
trades, but its setup policy did not use OB, FVG, trendline or channel.  This
script reconstructs each delayed-reclaim episode from the declared session
range, verifies the recorded boundary/extreme/reclaim geometry, and then asks
which source-defined OB/FVG observations were actually present.

The result is descriptive component attribution, not a new filter search:

* whether a footprint was already active at the boundary entry;
* whether a separate same-direction footprint formed during the response;
* whether it met the source's stated 2x quality heuristic;
* whether several timeframes independently represented the same zone;
* how those facts relate to the recorded trade outcome and net R.

This prevents a session-reclaim result from being misreported as evidence for
or against OB/FVG, while preserving potentially useful mechanisms inside a
family whose aggregate untouched performance was weak.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from data import load_range, resample
from domain_v3 import Candle, Side
from market_v7 import SessionLiquidityRange
import screen_v7 as session_base
import screen_v7_fixed  # noqa: F401 -- installs unit-stable range prices
from source_footprints import SourceFVG, SourceOrderBlock, detect_source_footprints
from trade_semantic_audit import Bar, ENTRY_EVENTS, audit_setup_lifecycle


TIMEFRAMES = (1, 5, 15, 60)


def records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return [
        {str(key): value for key, value in row.items()}
        for row in frame.to_dict("records")
    ]


def to_candles(frame: pd.DataFrame) -> list[Candle]:
    return [
        Candle(
            ts_open_ns=int(row.open_time_dt.value),
            ts_close_ns=int(row.close_time_dt.value),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in frame.itertuples(index=False)
    ]


def to_bars(symbol: str, frame: pd.DataFrame) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            open_time_ns=int(row.open_time_dt.value),
            close_time_ns=int(row.close_time_dt.value),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
        )
        for row in frame.itertuples(index=False)
    ]


def accepted_break(
    liquidity_range: SessionLiquidityRange,
    side: Side,
    close: float,
    multiple: float,
) -> bool:
    distance = liquidity_range.width * multiple
    if side is Side.LONG:
        return close <= liquidity_range.low - distance
    return close >= liquidity_range.high + distance


def reconstruct_delayed_episode(
    *,
    setup: Mapping[str, object],
    liquidity_range: SessionLiquidityRange,
    signal_frame: pd.DataFrame,
    accepted_break_widths: float,
) -> dict[str, object]:
    """Replay the v7 delayed-reclaim state machine and expose its timeline."""
    expected_side = Side(int(setup["side"]))
    observed_time_ns = int(setup["observed_time_ns"])
    state_side: Side | None = None
    outside_time_ns: int | None = None
    extreme: float | None = None
    extreme_time_ns: int | None = None
    outside_bars = 0
    selected = signal_frame[
        (signal_frame["open_time_dt"].astype("int64") >= liquidity_range.trade_start_ns)
        & (signal_frame["open_time_dt"].astype("int64") < liquidity_range.trade_end_ns)
        & (signal_frame["close_time_dt"].astype("int64") <= observed_time_ns)
    ]
    for row in selected.itertuples(index=False):
        open_ns = int(row.open_time_dt.value)
        close_ns = int(row.close_time_dt.value)
        low = float(row.low)
        high = float(row.high)
        close = float(row.close)
        lower_cross = low < liquidity_range.low
        upper_cross = high > liquidity_range.high
        if lower_cross and upper_cross:
            return {
                "episode_match": False,
                "episode_failure": "TWO_SIDED_SAME_BAR_BEFORE_RECORDED_RECLAIM",
                "failure_time_ns": close_ns,
            }

        if state_side is not None:
            outside_bars += 1
            if state_side is Side.LONG and low < float(extreme):
                extreme = low
                extreme_time_ns = close_ns
            elif state_side is Side.SHORT and high > float(extreme):
                extreme = high
                extreme_time_ns = close_ns
            reclaimed = (
                close >= liquidity_range.low
                if state_side is Side.LONG
                else close <= liquidity_range.high
            )
            if reclaimed:
                if close_ns != observed_time_ns or state_side is not expected_side:
                    return {
                        "episode_match": False,
                        "episode_failure": "EARLIER_OR_WRONG_SIDE_RECLAIM",
                        "failure_time_ns": close_ns,
                        "outside_side": int(state_side),
                    }
                expected_extreme = float(setup["formation_extreme"])
                return {
                    "episode_match": abs(float(extreme) - expected_extreme) < 1e-9,
                    "episode_failure": (
                        None
                        if abs(float(extreme) - expected_extreme) < 1e-9
                        else "FORMATION_EXTREME_MISMATCH"
                    ),
                    "outside_side": int(state_side),
                    "outside_time_ns": outside_time_ns,
                    "extreme": float(extreme),
                    "extreme_time_ns": extreme_time_ns,
                    "reclaim_time_ns": close_ns,
                    "outside_bars_including_reclaim": outside_bars,
                    "entry_boundary": (
                        liquidity_range.low
                        if state_side is Side.LONG
                        else liquidity_range.high
                    ),
                }
            if accepted_break(
                liquidity_range,
                state_side,
                close,
                accepted_break_widths,
            ):
                return {
                    "episode_match": False,
                    "episode_failure": "ACCEPTED_BREAK_BEFORE_RECORDED_RECLAIM",
                    "failure_time_ns": close_ns,
                }
            continue

        if lower_cross:
            if close >= liquidity_range.low:
                return {
                    "episode_match": False,
                    "episode_failure": "IMMEDIATE_FAKEOUT_BEFORE_DELAYED_SETUP",
                    "failure_time_ns": close_ns,
                }
            if accepted_break(
                liquidity_range,
                Side.LONG,
                close,
                accepted_break_widths,
            ):
                return {
                    "episode_match": False,
                    "episode_failure": "ACCEPTED_LOWER_BREAK_BEFORE_SETUP",
                    "failure_time_ns": close_ns,
                }
            state_side = Side.LONG
            outside_time_ns = close_ns
            extreme = low
            extreme_time_ns = close_ns
            outside_bars = 1
            continue

        if upper_cross:
            if close <= liquidity_range.high:
                return {
                    "episode_match": False,
                    "episode_failure": "IMMEDIATE_FAKEOUT_BEFORE_DELAYED_SETUP",
                    "failure_time_ns": close_ns,
                }
            if accepted_break(
                liquidity_range,
                Side.SHORT,
                close,
                accepted_break_widths,
            ):
                return {
                    "episode_match": False,
                    "episode_failure": "ACCEPTED_UPPER_BREAK_BEFORE_SETUP",
                    "failure_time_ns": close_ns,
                }
            state_side = Side.SHORT
            outside_time_ns = close_ns
            extreme = high
            extreme_time_ns = close_ns
            outside_bars = 1

    return {
        "episode_match": False,
        "episode_failure": "RECORDED_RECLAIM_NOT_RECONSTRUCTED",
    }


def bars_between(
    frame: pd.DataFrame,
    after_close_ns: int,
    before_open_ns: int,
) -> pd.DataFrame:
    after = pd.Timestamp(after_close_ns, unit="ns", tz="UTC")
    before = pd.Timestamp(before_open_ns, unit="ns", tz="UTC")
    return frame[
        (frame["open_time_dt"] > after)
        & (frame["open_time_dt"] < before)
    ]


def ob_active(
    footprint: SourceOrderBlock,
    one_minute: pd.DataFrame,
    before_open_ns: int,
) -> bool:
    selected = bars_between(
        one_minute,
        footprint.observed_time_ns,
        before_open_ns,
    )
    if selected.empty:
        return True
    if footprint.side is Side.LONG:
        return not bool((selected["low"] <= footprint.invalidation).any())
    return not bool((selected["high"] >= footprint.invalidation).any())


def fvg_untouched(
    footprint: SourceFVG,
    one_minute: pd.DataFrame,
    before_open_ns: int,
) -> bool:
    selected = bars_between(
        one_minute,
        footprint.observed_time_ns,
        before_open_ns,
    )
    if selected.empty:
        return True
    return not bool(
        (
            (selected["low"] <= footprint.zone_high)
            & (selected["high"] >= footprint.zone_low)
        ).any(),
    )


def temporal_role(
    *,
    observed_time_ns: int,
    outside_time_ns: int,
    reclaim_time_ns: int,
    first_retest_open_ns: int | None,
) -> str:
    if observed_time_ns == reclaim_time_ns:
        return "FORMED_ON_RECLAIM_CLOSE"
    if outside_time_ns <= observed_time_ns < reclaim_time_ns:
        return "FORMED_DURING_OUTSIDE_EPISODE"
    if observed_time_ns < outside_time_ns:
        return "PREEXISTING_BEFORE_OUTSIDE_EPISODE"
    if first_retest_open_ns is not None and observed_time_ns < first_retest_open_ns:
        return "FORMED_AFTER_CONFIRMATION_BEFORE_FIRST_RETEST"
    return "AFTER_FIRST_RETEST_OR_UNRELATED"


def footprint_row(
    footprint: SourceOrderBlock | SourceFVG,
    *,
    setup: Mapping[str, object],
    one_minute: pd.DataFrame,
    outside_time_ns: int,
    reclaim_time_ns: int,
    first_retest_open_ns: int | None,
) -> dict[str, object]:
    side = Side(int(setup["side"]))
    entry = float(setup["entry"])
    role = temporal_role(
        observed_time_ns=footprint.observed_time_ns,
        outside_time_ns=outside_time_ns,
        reclaim_time_ns=reclaim_time_ns,
        first_retest_open_ns=first_retest_open_ns,
    )
    before_reclaim_open = reclaim_time_ns + 1
    before_entry_open = (
        first_retest_open_ns
        if first_retest_open_ns is not None
        else reclaim_time_ns + 1
    )
    row = asdict(footprint)
    row.update(
        {
            "setup_id": str(setup["setup_id"]),
            "event_symbol": str(setup["symbol"]),
            "kind": (
                "ORDER_BLOCK"
                if isinstance(footprint, SourceOrderBlock)
                else "FVG"
            ),
            "same_direction_as_event": footprint.side is side,
            "entry_inside_zone": footprint.zone_low <= entry <= footprint.zone_high,
            "temporal_role": role,
        },
    )
    if isinstance(footprint, SourceOrderBlock):
        row["active_at_reclaim"] = ob_active(
            footprint,
            one_minute,
            before_reclaim_open,
        )
        row["active_at_first_retest"] = ob_active(
            footprint,
            one_minute,
            before_entry_open,
        )
        row["source_quality_2x"] = footprint.source_two_x_quality
        row["strict_source_quality"] = footprint.source_two_x_quality
        row["lifecycle_note"] = "ACTIVE_UNTIL_FORMATION_EXTREME_BREACH"
    else:
        row["active_at_reclaim"] = fvg_untouched(
            footprint,
            one_minute,
            before_reclaim_open,
        )
        row["active_at_first_retest"] = fvg_untouched(
            footprint,
            one_minute,
            before_entry_open,
        )
        row["source_quality_2x"] = footprint.source_two_x_quality
        row["strict_source_quality"] = footprint.source_two_x_quality
        row["lifecycle_note"] = "FIRST_RETRACEMENT_WAVE_BOUNDARY_UNRESOLVED"
    return row


def summarize_feature(
    setup_rows: Sequence[Mapping[str, object]],
    feature: str,
) -> dict[str, object]:
    output: dict[str, object] = {}
    for value in (False, True):
        selected = [row for row in setup_rows if bool(row.get(feature)) is value]
        traded = [row for row in selected if row.get("recorded_trade")]
        output[str(value).lower()] = {
            "setups": len(selected),
            "trades": len(traded),
            "targets": sum(row.get("trade_outcome") == "TARGET" for row in traded),
            "stops": sum(row.get("trade_outcome") == "STOP" for row in traded),
            "net_r_sum": float(sum(float(row.get("net_r", 0.0)) for row in traded)),
            "net_r_mean": (
                None
                if not traded
                else float(
                    sum(float(row.get("net_r", 0.0)) for row in traded)
                    / len(traded)
                )
            ),
        }
    return output


def run(args: argparse.Namespace) -> dict[str, object]:
    run_dir = args.run_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_document = json.loads(
        (run_dir / "run.json").read_text(encoding="utf-8"),
    )
    config = run_document["config"]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    build_start = start - timedelta(days=int(config.get("warmup_days", 5)))
    families = session_base.parse_families(str(config["families"]))
    setups = records(pd.read_csv(run_dir / "setups.csv"))
    trades = records(pd.read_csv(run_dir / "trades.csv"))
    trade_by_plan = {str(row["plan_id"]): row for row in trades}
    setup_audit = records(pd.read_csv(args.setup_audit.resolve()))
    setup_audit_by_plan = {str(row["plan_id"]): row for row in setup_audit}
    symbols = sorted({str(row["symbol"]) for row in setups})

    one_by_symbol = {
        symbol: load_range(symbol, build_start, end, args.cache.resolve())
        for symbol in symbols
    }
    signal_by_symbol = {
        symbol: resample(frame, int(config["signal_minutes"]))
        for symbol, frame in one_by_symbol.items()
    }
    bars_by_symbol = {
        symbol: to_bars(symbol, frame)
        for symbol, frame in one_by_symbol.items()
    }
    ranges: dict[str, SessionLiquidityRange] = {}
    for symbol in symbols:
        for item in session_base.build_ranges(
            symbol,
            one_by_symbol[symbol],
            build_start,
            end,
            families,
        ):
            ranges[item.range_id] = item

    footprints: dict[str, list[SourceOrderBlock | SourceFVG]] = {}
    for symbol in symbols:
        rows: list[SourceOrderBlock | SourceFVG] = []
        for minutes in TIMEFRAMES:
            frame = (
                one_by_symbol[symbol]
                if minutes == 1
                else resample(one_by_symbol[symbol], minutes)
            )
            update = detect_source_footprints(
                symbol,
                to_candles(frame),
                minutes,
            )
            rows.extend(update.order_blocks)
            rows.extend(update.fvgs)
        footprints[symbol] = rows

    setup_rows: list[dict[str, object]] = []
    footprint_rows: list[dict[str, object]] = []
    casebook: list[dict[str, object]] = []
    for setup in setups:
        setup_id = str(setup["setup_id"])
        pool_id = str(setup["source_pool_id"])
        liquidity_range = ranges.get(pool_id)
        if liquidity_range is None:
            episode = {
                "episode_match": False,
                "episode_failure": "SOURCE_POOL_NOT_REBUILT",
            }
        else:
            episode = reconstruct_delayed_episode(
                setup=setup,
                liquidity_range=liquidity_range,
                signal_frame=signal_by_symbol[str(setup["symbol"])],
                accepted_break_widths=float(config["accepted_break_widths"]),
            )
        lifecycle = audit_setup_lifecycle(
            setup,
            bars_by_symbol[str(setup["symbol"])],
        )
        first_retest_open_ns = lifecycle.event_open_time_ns
        outside_time_ns = episode.get("outside_time_ns")
        reclaim_time_ns = episode.get("reclaim_time_ns")
        relevant: list[dict[str, object]] = []
        if outside_time_ns is not None and reclaim_time_ns is not None:
            for footprint in footprints[str(setup["symbol"])]:
                role = temporal_role(
                    observed_time_ns=footprint.observed_time_ns,
                    outside_time_ns=int(outside_time_ns),
                    reclaim_time_ns=int(reclaim_time_ns),
                    first_retest_open_ns=first_retest_open_ns,
                )
                if role == "AFTER_FIRST_RETEST_OR_UNRELATED":
                    continue
                row = footprint_row(
                    footprint,
                    setup=setup,
                    one_minute=one_by_symbol[str(setup["symbol"])],
                    outside_time_ns=int(outside_time_ns),
                    reclaim_time_ns=int(reclaim_time_ns),
                    first_retest_open_ns=first_retest_open_ns,
                )
                relevant.append(row)
                footprint_rows.append(row)

        aligned_entry = [
            row
            for row in relevant
            if row["same_direction_as_event"]
            and row["entry_inside_zone"]
            and row["active_at_first_retest"]
        ]
        response = [
            row
            for row in relevant
            if row["same_direction_as_event"]
            and row["temporal_role"] in {
                "FORMED_DURING_OUTSIDE_EPISODE",
                "FORMED_ON_RECLAIM_CLOSE",
                "FORMED_AFTER_CONFIRMATION_BEFORE_FIRST_RETEST",
            }
        ]
        aligned_obs = [row for row in aligned_entry if row["kind"] == "ORDER_BLOCK"]
        aligned_fvgs = [row for row in aligned_entry if row["kind"] == "FVG"]
        aligned_quality_obs = [
            row for row in aligned_obs if row["strict_source_quality"]
        ]
        aligned_strict_fvgs = [
            row for row in aligned_fvgs if row["strict_source_quality"]
        ]
        response_obs = [row for row in response if row["kind"] == "ORDER_BLOCK"]
        response_fvgs = [row for row in response if row["kind"] == "FVG"]
        response_quality_obs = [
            row for row in response_obs if row["strict_source_quality"]
        ]
        response_strict_fvgs = [
            row for row in response_fvgs if row["strict_source_quality"]
        ]
        aligned_timeframes = sorted(
            {int(row["timeframe_minutes"]) for row in aligned_entry},
        )
        response_timeframes = sorted(
            {int(row["timeframe_minutes"]) for row in response},
        )
        trade = trade_by_plan.get(setup_id)
        audit = setup_audit_by_plan.get(setup_id, {})
        result = {
            "setup_id": setup_id,
            "symbol": setup["symbol"],
            "family": setup["family"],
            "side": int(setup["side"]),
            "observed_time_ns": int(setup["observed_time_ns"]),
            "source_pool_id": pool_id,
            "entry": float(setup["entry"]),
            "stop": float(setup["stop"]),
            "far_target": float(setup["initial_target"]),
            **episode,
            "first_decisive_classification": lifecycle.classification,
            "first_decisive_event": lifecycle.event,
            "first_decisive_open_time_ns": lifecycle.event_open_time_ns,
            "setup_disposition": audit.get("disposition"),
            "recorded_trade": trade is not None,
            "trade_outcome": None if trade is None else trade["outcome"],
            "gross_rr": None if trade is None else float(trade["gross_rr"]),
            "net_r": None if trade is None else float(trade["net_r"]),
            "net_pnl": None if trade is None else float(trade["net_pnl"]),
            "hold_minutes": None if trade is None else int(trade["hold_minutes"]),
            "aligned_entry_ob_count": len(aligned_obs),
            "aligned_entry_quality_ob_count": len(aligned_quality_obs),
            "aligned_entry_fvg_count": len(aligned_fvgs),
            "aligned_entry_strict_fvg_count": len(aligned_strict_fvgs),
            "response_ob_count": len(response_obs),
            "response_quality_ob_count": len(response_quality_obs),
            "response_fvg_count": len(response_fvgs),
            "response_strict_fvg_count": len(response_strict_fvgs),
            "aligned_entry_timeframes": aligned_timeframes,
            "response_timeframes": response_timeframes,
            "has_aligned_entry_ob": bool(aligned_obs),
            "has_aligned_entry_quality_ob": bool(aligned_quality_obs),
            "has_aligned_entry_fvg": bool(aligned_fvgs),
            "has_aligned_entry_strict_fvg": bool(aligned_strict_fvgs),
            "has_response_ob": bool(response_obs),
            "has_response_quality_ob": bool(response_quality_obs),
            "has_response_fvg": bool(response_fvgs),
            "has_response_strict_fvg": bool(response_strict_fvgs),
            "has_distinct_source_footprint_role": bool(aligned_entry or response),
            "multi_timeframe_aligned_entry": len(aligned_timeframes) >= 2,
            "multi_timeframe_response": len(response_timeframes) >= 2,
            "aligned_entry_footprint_ids": [
                str(row["footprint_id"]) for row in aligned_entry
            ],
            "response_footprint_ids": [
                str(row["footprint_id"]) for row in response
            ],
        }
        setup_rows.append(result)
        casebook.append(
            {
                "setup": setup,
                "episode": episode,
                "lifecycle": asdict(lifecycle),
                "setup_audit": audit,
                "trade": trade,
                "summary": result,
                "relevant_footprints": relevant,
            },
        )

    pd.DataFrame(setup_rows).to_csv(
        output / "generic_setup_footprint_audit.csv",
        index=False,
    )
    pd.DataFrame(footprint_rows).to_csv(
        output / "generic_relevant_footprints.csv",
        index=False,
    )
    (output / "generic_setup_casebook.jsonl").write_text(
        "".join(
            json.dumps(row, sort_keys=True, default=str) + "\n"
            for row in casebook
        ),
        encoding="utf-8",
    )
    (output / "generic_relevant_footprints.jsonl").write_text(
        "".join(
            json.dumps(row, sort_keys=True, default=str) + "\n"
            for row in footprint_rows
        ),
        encoding="utf-8",
    )

    largest = max(
        (row for row in setup_rows if row["recorded_trade"]),
        key=lambda row: float(row["net_pnl"]),
        default=None,
    )
    features = (
        "has_aligned_entry_ob",
        "has_aligned_entry_quality_ob",
        "has_aligned_entry_fvg",
        "has_aligned_entry_strict_fvg",
        "has_response_ob",
        "has_response_quality_ob",
        "has_response_fvg",
        "has_response_strict_fvg",
        "has_distinct_source_footprint_role",
        "multi_timeframe_aligned_entry",
        "multi_timeframe_response",
    )
    summary = {
        "setups": len(setup_rows),
        "trades": sum(bool(row["recorded_trade"]) for row in setup_rows),
        "targets": sum(row["trade_outcome"] == "TARGET" for row in setup_rows),
        "stops": sum(row["trade_outcome"] == "STOP" for row in setup_rows),
        "episode_reconstruction_mismatches": sum(
            not bool(row.get("episode_match")) for row in setup_rows
        ),
        "setup_dispositions": dict(
            Counter(str(row.get("setup_disposition")) for row in setup_rows),
        ),
        "feature_outcomes": {
            feature: summarize_feature(setup_rows, feature)
            for feature in features
        },
        "largest_winner": largest,
        "interpretation_limits": [
            "The v10 policy did not use these footprints to decide entry; this is post-run attribution, not causal strategy evidence.",
            "Only eleven trades and two winners exist; one +10.9R SOL trade dominates the sample.",
            "The FVG forming-wave/first-retracement lifecycle remains unresolved, so untouched-zone freshness is a provisional proxy.",
            "A footprint count is not a score, risk multiplier or permission to add an AND filter.",
            "Trendline and channel context require a separate geometric audit before the complete source strategy can be judged."
        ],
    }
    (output / "generic_footprint_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--setup-audit", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
