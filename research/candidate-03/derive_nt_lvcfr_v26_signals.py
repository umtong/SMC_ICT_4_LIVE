#!/usr/bin/env python3
"""Derive failed-reversal-trap continuations from V24 causal opportunities.

V24 established a frequent reversal setup but the reversal direction failed
across all three fixed BTC weeks. V26 does not invert outcomes retrospectively.
It waits for the reversal thesis to fail causally before considering a trade:

1. Start from a V24 five-minute sweep plus one-minute CHoCH/FVG-defense state.
2. Do not enter the V24 reversal.
3. Require a completed close through the defended FVG far edge with futures and
   spot aggressive flow in the original sweep direction.
4. Wait for a retest of the failed gap from the other side and a completed
   rejection in the continuation direction. A full gap reclaim cancels it.
5. Enter on the next native quote with local failed-gap invalidation. Target the
   next directional external liquidity from the causal pre-sweep four-hour
   range, with the original sweep extreme as a protection waypoint.

No return labels, fill simulation, PnL or NAV calculations occur here.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from derive_nt_lvcfr_v24_signals import OneBar, one_bar, rolling_atr
from nt_lvcfr_data import NS_PER_MINUTE, load_kline_minutes

FAILED_REVERSAL_TRAP_CONTINUATION = "FAILED_REVERSAL_FVG_TRAP_CONTINUATION"


@dataclass(frozen=True, slots=True)
class Failure:
    index: int
    bar: OneBar
    source_direction: int


def find_reversal_failure(
    futures: list[OneBar],
    spot: list[OneBar],
    *,
    start_index: int,
    source_direction: int,
    gap_lower: float,
    gap_upper: float,
    expiry_minutes: int,
) -> tuple[Failure | None, str]:
    if source_direction not in (-1, 1):
        raise ValueError("source_direction must be -1 or 1")
    continuation = -source_direction
    last = min(len(futures), start_index + expiry_minutes)
    for index in range(start_index, last):
        bar = futures[index]
        failed = bar.close <= gap_lower if source_direction > 0 else bar.close >= gap_upper
        if not failed:
            continue
        if continuation * bar.flow <= 0.0:
            continue
        if continuation * spot[index].flow <= 0.0:
            continue
        return Failure(index=index, bar=bar, source_direction=source_direction), "FAILED_REVERSAL_CONFIRMED"
    return None, "REVERSAL_FAILURE_UNRESOLVED"


def find_failed_gap_retest(
    futures: list[OneBar],
    spot: list[OneBar],
    *,
    start_index: int,
    source_direction: int,
    gap_lower: float,
    gap_upper: float,
    expiry_minutes: int,
) -> tuple[int, OneBar, int] | tuple[None, None, str]:
    continuation = -source_direction
    touches = 0
    last = min(len(futures), start_index + expiry_minutes)
    for index in range(start_index, last):
        bar = futures[index]
        if source_direction > 0 and bar.close >= gap_upper:
            return None, None, "FAILED_BULLISH_GAP_FULLY_RECLAIMED"
        if source_direction < 0 and bar.close <= gap_lower:
            return None, None, "FAILED_BEARISH_GAP_FULLY_RECLAIMED"
        touched = bar.low <= gap_upper and bar.high >= gap_lower
        if not touched:
            continue
        touches += 1
        rejected = (
            bar.close < gap_lower and bar.close < bar.open
            if continuation < 0
            else bar.close > gap_upper and bar.close > bar.open
        )
        if rejected and continuation * bar.flow > 0.0 and continuation * spot[index].flow > 0.0:
            return index, bar, touches
    return None, None, "FAILED_GAP_RETEST_UNRESOLVED"


def derive_v26(
    *,
    prepared_root: Path,
    source_signals: Path,
    output_signals: Path,
    output_manifest: Path,
    failure_expiry_minutes: int = 10,
    retest_expiry_minutes: int = 10,
    external_lookback_minutes: int = 240,
    atr_minutes: int = 20,
    stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if failure_expiry_minutes <= 0 or retest_expiry_minutes <= 0:
        raise ValueError("failure/retest expiry must be positive")
    if external_lookback_minutes < 60 or atr_minutes <= 0:
        raise ValueError("lookbacks must be positive")
    if stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be non-negative")

    raw_root = prepared_root / "raw"
    futures_facts = load_kline_minutes(sorted((raw_root / "futures_kline").glob("*.zip")))
    spot_facts = load_kline_minutes(sorted((raw_root / "spot_kline").glob("*.zip")))
    futures_map = {fact.minute_index: fact for fact in futures_facts}
    spot_map = {fact.minute_index: fact for fact in spot_facts}
    aligned = sorted(set(futures_map) & set(spot_map))
    futures = [one_bar(futures_map[minute]) for minute in aligned]
    spot = [one_bar(spot_map[minute]) for minute in aligned]
    index_by_start = {bar.start_minute: index for index, bar in enumerate(futures)}
    atr = rolling_atr(futures, atr_minutes)
    source = json.loads(source_signals.read_text(encoding="utf-8"))

    derived: list[dict[str, Any]] = []
    no_trade_reasons: dict[str, int] = {}
    source_state_counts: dict[str, int] = {}
    gross_rrs: list[float] = []
    deduplicated = 0
    seen: set[tuple[int, int]] = set()

    def count(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    for original in sorted(source, key=lambda item: int(item["confirm_time_ns"])):
        source_state = str(original.get("scenario_kind", "UNKNOWN"))
        count(source_state_counts, source_state)
        source_direction = int(original["direction"])
        details = dict(original.get("details", {}))
        try:
            gap_lower = float(details["fvg_lower"])
            gap_upper = float(details["fvg_upper"])
            sweep_start_minute = int(details["sweep_start_time_ns"]) // NS_PER_MINUTE
            sweep_high = float(details["sweep_high"])
            sweep_low = float(details["sweep_low"])
        except (KeyError, TypeError, ValueError):
            count(no_trade_reasons, "MISSING_V24_CAUSAL_STRUCTURE")
            continue
        confirm_start_minute = int(original["confirm_time_ns"]) // NS_PER_MINUTE
        start_index = index_by_start.get(confirm_start_minute)
        if start_index is None:
            count(no_trade_reasons, "MISSING_FAILURE_START_ALIGNMENT")
            continue
        failure, reason = find_reversal_failure(
            futures,
            spot,
            start_index=start_index,
            source_direction=source_direction,
            gap_lower=gap_lower,
            gap_upper=gap_upper,
            expiry_minutes=failure_expiry_minutes,
        )
        if failure is None:
            count(no_trade_reasons, reason)
            continue
        retest = find_failed_gap_retest(
            futures,
            spot,
            start_index=failure.index + 1,
            source_direction=source_direction,
            gap_lower=gap_lower,
            gap_upper=gap_upper,
            expiry_minutes=retest_expiry_minutes,
        )
        if retest[0] is None:
            count(no_trade_reasons, str(retest[2]))
            continue
        retest_index = int(retest[0])
        retest_bar = retest[1]
        touches = int(retest[2])
        assert isinstance(retest_bar, OneBar)
        continuation = -source_direction

        prior_start = sweep_start_minute - external_lookback_minutes
        prior_rows = [futures_map.get(minute) for minute in range(prior_start, sweep_start_minute)]
        if any(row is None for row in prior_rows) or len(prior_rows) != external_lookback_minutes:
            count(no_trade_reasons, "MISSING_PRE_SWEEP_EXTERNAL_RANGE")
            continue
        prior = [row for row in prior_rows if row is not None]
        external_low = min(row.low for row in prior)
        external_high = max(row.high for row in prior)
        entry_reference = retest_bar.close
        target = external_high if continuation > 0 else external_low
        if continuation * (target - entry_reference) <= 0.0:
            count(no_trade_reasons, "DIRECTIONAL_EXTERNAL_TARGET_NOT_AHEAD")
            continue
        at = atr.get(retest_bar.end_minute)
        if at is None or not math.isfinite(at) or at <= 0.0:
            count(no_trade_reasons, "MISSING_CAUSAL_ATR")
            continue
        if continuation > 0:
            stop_anchor = min(gap_lower, retest_bar.low)
            waypoint_candidate = sweep_high
        else:
            stop_anchor = max(gap_upper, retest_bar.high)
            waypoint_candidate = sweep_low
        stop = stop_anchor - continuation * stop_buffer_atr * at
        risk_distance = continuation * (entry_reference - stop)
        reward_distance = continuation * (target - entry_reference)
        if risk_distance <= 0.0 or not math.isfinite(stop):
            count(no_trade_reasons, "NON_EXECUTABLE_FAILED_GAP_INVALIDATION")
            continue
        gross_rr = reward_distance / risk_distance
        waypoint = (
            waypoint_candidate
            if continuation * (waypoint_candidate - entry_reference) > 0.0
            and continuation * (target - waypoint_candidate) >= 0.0
            else None
        )
        confirm_ns = retest_bar.end_minute * NS_PER_MINUTE
        key = (confirm_ns, continuation)
        if key in seen:
            deduplicated += 1
            continue
        seen.add(key)
        suffix = sha256(
            f"{confirm_ns}|{continuation}|{gap_lower:.12g}|{gap_upper:.12g}".encode()
        ).hexdigest()[:16]
        routed_details = details
        routed_details.update(
            {
                "scenario_kind": FAILED_REVERSAL_TRAP_CONTINUATION,
                "entry_kind": "CONTINUATION",
                "source_v24_scenario_id": original["scenario_id"],
                "source_v24_state": source_state,
                "source_v24_direction": source_direction,
                "routed_direction": continuation,
                "entry_sequence": [
                    "V24_REVERSAL_OPPORTUNITY_WITHOUT_ENTRY",
                    "FVG_FAR_EDGE_FAILURE_WITH_FUTURES_AND_SPOT_FLOW",
                    "FAILED_GAP_RETEST_FROM_CONTINUATION_SIDE",
                    "COMPLETED_CONTINUATION_REJECTION",
                    "NEXT_NATIVE_QUOTE_ENTRY",
                ],
                "failure_end_time_ns": failure.bar.end_minute * NS_PER_MINUTE,
                "failure_wait_minutes": failure.index - start_index + 1,
                "failure_open": failure.bar.open,
                "failure_high": failure.bar.high,
                "failure_low": failure.bar.low,
                "failure_close": failure.bar.close,
                "failure_flow": failure.bar.flow,
                "retest_end_time_ns": confirm_ns,
                "retest_wait_minutes": retest_index - failure.index,
                "retest_touch_count": touches,
                "retest_open": retest_bar.open,
                "retest_high": retest_bar.high,
                "retest_low": retest_bar.low,
                "retest_close": retest_bar.close,
                "retest_flow": retest_bar.flow,
                "external_lookback_minutes": external_lookback_minutes,
                "directional_external_target": target,
                "structural_waypoint": waypoint,
                "stop_anchor": stop_anchor,
                "stop_buffer_atr": stop_buffer_atr,
                "gross_structural_rr_at_retest": gross_rr,
            }
        )
        signal: dict[str, Any] = {
            "scenario_id": f"NT-LVCFR-V26-{FAILED_REVERSAL_TRAP_CONTINUATION}-{suffix}",
            "scenario_kind": FAILED_REVERSAL_TRAP_CONTINUATION,
            "entry_kind": "CONTINUATION",
            "confirm_time_ns": confirm_ns,
            "eligible_time_ns": confirm_ns,
            "direction": continuation,
            "initial_stop": stop,
            "atr": at,
            "first_start_time_ns": int(original["first_start_time_ns"]),
            "first_end_time_ns": int(original["first_end_time_ns"]),
            "structural_target": target,
            "target_mode": "STRUCTURAL_LIQUIDITY_OBJECTIVE",
            "disable_rapid_failure_reversal": True,
            "details": routed_details,
        }
        if waypoint is not None:
            signal["structural_protection_trigger"] = waypoint
        derived.append(signal)
        gross_rrs.append(gross_rr)

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(derived, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ordered_rrs = sorted(gross_rrs)
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v26-failed-reversal-trap-continuation",
        "engine_status": "causal_schedule_only_no_backtest",
        "source_signal_count": len(source),
        "derived_signal_count": len(derived),
        "source_state_counts": dict(sorted(source_state_counts.items())),
        "output_state_counts": ({FAILED_REVERSAL_TRAP_CONTINUATION: len(derived)} if derived else {}),
        "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "deduplicated": deduplicated,
        "failure_expiry_minutes": failure_expiry_minutes,
        "retest_expiry_minutes": retest_expiry_minutes,
        "external_lookback_minutes": external_lookback_minutes,
        "atr_minutes": atr_minutes,
        "stop_buffer_atr": stop_buffer_atr,
        "minimum_gross_structural_rr": min(gross_rrs) if gross_rrs else None,
        "median_gross_structural_rr": ordered_rrs[len(ordered_rrs) // 2] if ordered_rrs else None,
        "selection_policy": (
            "causal failure of V24 reversal thesis before entry; failed-gap retest; "
            "no return-fit threshold search; native structural target cost gate required"
        ),
        "source_signals": str(source_signals),
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return derived


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--source-signals", type=Path)
    parser.add_argument("--output-signals", type=Path)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--failure-expiry-minutes", type=int, default=10)
    parser.add_argument("--retest-expiry-minutes", type=int, default=10)
    parser.add_argument("--external-lookback-minutes", type=int, default=240)
    parser.add_argument("--atr-minutes", type=int, default=20)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    source = (args.source_signals or (prepared / "signals-v24.json")).resolve()
    output = (args.output_signals or (prepared / "signals.json")).resolve()
    signals = derive_v26(
        prepared_root=prepared,
        source_signals=source,
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        failure_expiry_minutes=args.failure_expiry_minutes,
        retest_expiry_minutes=args.retest_expiry_minutes,
        external_lookback_minutes=args.external_lookback_minutes,
        atr_minutes=args.atr_minutes,
        stop_buffer_atr=args.stop_buffer_atr,
    )
    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v26-failed-reversal-trap-continuation",
                "signals": len(signals),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
