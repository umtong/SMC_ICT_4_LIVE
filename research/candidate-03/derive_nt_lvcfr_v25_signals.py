#!/usr/bin/env python3
"""Derive external-liquidity absorption reversals from causal order-flow response.

V25 trades a recurring auction failure rather than a named candle pattern:

1. A completed three-minute futures window consumes an extreme amount of
   aggressive flow relative to the previous four hours, but produces an
   unusually weak same-direction price response.
2. That inefficient flow probes beyond the preceding sixty-minute external
   high/low and closes back inside, while spot does not accept beyond its own
   external boundary.
3. Within three completed minutes, price closes through the event midpoint in
   the reversal direction with opposite futures aggressive flow.
4. Entry occurs on the next native quote. Invalidation remains beyond the
   absorbed event extreme plus 0.20 one-minute ATR. The opposite side of the
   pre-event sixty-minute range is the final liquidity objective and range
   equilibrium is a protection waypoint.

All thresholds are rolling causal quartiles of the same three-minute horizon.
There is no return-fit search or PnL calculation in this module.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence

from nt_lvcfr_data import NS_PER_MINUTE, MinuteFact, load_kline_minutes, load_open_interest

OI_EXPANSION_ABSORPTION_REVERSAL = "OI_EXPANSION_EXTERNAL_ABSORPTION_REVERSAL"
OI_CONTRACTION_ABSORPTION_REVERSAL = "OI_CONTRACTION_EXTERNAL_ABSORPTION_REVERSAL"


@dataclass(frozen=True, slots=True)
class OneBar:
    start_minute: int
    open: float
    high: float
    low: float
    close: float
    notional: float
    signed_notional: float

    @property
    def end_minute(self) -> int:
        return self.start_minute + 1

    @property
    def flow(self) -> float:
        return self.signed_notional / self.notional if self.notional > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class FlowWindow:
    start_index: int
    end_index: int
    open: float
    high: float
    low: float
    close: float
    gross_notional: float
    signed_notional: float

    @property
    def direction(self) -> int:
        if self.signed_notional > 0.0:
            return 1
        if self.signed_notional < 0.0:
            return -1
        return 0

    @property
    def absolute_flow(self) -> float:
        return abs(self.signed_notional) / self.gross_notional if self.gross_notional > 0.0 else 0.0

    @property
    def directional_progress_bp(self) -> float:
        if self.direction == 0 or self.open <= 0.0:
            return 0.0
        return self.direction * (self.close / self.open - 1.0) * 10_000.0

    @property
    def response_score(self) -> float:
        return self.directional_progress_bp / max(self.absolute_flow, 1e-9)


def one_bar(fact: MinuteFact) -> OneBar:
    return OneBar(
        start_minute=fact.minute_index,
        open=fact.open,
        high=fact.high,
        low=fact.low,
        close=fact.close,
        notional=fact.notional,
        signed_notional=fact.signed_notional,
    )


def make_flow_window(bars: Sequence[OneBar], start: int, length: int) -> FlowWindow | None:
    rows = bars[start : start + length]
    if len(rows) != length:
        return None
    if [row.start_minute for row in rows] != list(range(rows[0].start_minute, rows[0].start_minute + length)):
        return None
    return FlowWindow(
        start_index=start,
        end_index=start + length,
        open=rows[0].open,
        high=max(row.high for row in rows),
        low=min(row.low for row in rows),
        close=rows[-1].close,
        gross_notional=sum(row.notional for row in rows),
        signed_notional=sum(row.signed_notional for row in rows),
    )


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def rolling_atr(bars: Sequence[OneBar], window: int) -> dict[int, float]:
    history: deque[float] = deque(maxlen=window)
    previous_close: float | None = None
    output: dict[int, float] = {}
    for bar in bars:
        true_range = bar.high - bar.low
        if previous_close is not None:
            true_range = max(true_range, abs(bar.high - previous_close), abs(bar.low - previous_close))
        history.append(true_range)
        if len(history) == window:
            output[bar.end_minute] = sum(history) / window
        previous_close = bar.close
    return output


def find_absorption_confirmation(
    bars: Sequence[OneBar],
    *,
    event: FlowWindow,
    reversal_direction: int,
    expiry_minutes: int,
) -> tuple[int, OneBar] | tuple[None, None]:
    if reversal_direction not in (-1, 1):
        raise ValueError("reversal_direction must be -1 or 1")
    midpoint = (event.high + event.low) / 2.0
    last = min(len(bars), event.end_index + expiry_minutes)
    for index in range(event.end_index, last):
        current = bars[index]
        crossed = current.close > midpoint if reversal_direction > 0 else current.close < midpoint
        body = current.close > current.open if reversal_direction > 0 else current.close < current.open
        opposite_flow = reversal_direction * current.flow > 0.0
        if crossed and body and opposite_flow:
            return index, current
    return None, None


def derive_v25(
    *,
    prepared_root: Path,
    output_signals: Path,
    output_manifest: Path,
    event_minutes: int = 3,
    baseline_minutes: int = 240,
    external_range_minutes: int = 60,
    confirmation_expiry_minutes: int = 3,
    atr_minutes: int = 20,
    stop_buffer_atr: float = 0.20,
) -> list[dict[str, Any]]:
    if event_minutes <= 0 or baseline_minutes < 60 or external_range_minutes <= event_minutes:
        raise ValueError("invalid time windows")
    if confirmation_expiry_minutes <= 0 or atr_minutes <= 0:
        raise ValueError("confirmation and ATR windows must be positive")
    if stop_buffer_atr < 0.0:
        raise ValueError("stop_buffer_atr must be non-negative")

    raw_root = prepared_root / "raw"
    futures_facts = load_kline_minutes(sorted((raw_root / "futures_kline").glob("*.zip")))
    spot_facts = load_kline_minutes(sorted((raw_root / "spot_kline").glob("*.zip")))
    oi = load_open_interest(sorted((raw_root / "open_interest").glob("*.zip")))
    futures_map = {fact.minute_index: fact for fact in futures_facts}
    spot_map = {fact.minute_index: fact for fact in spot_facts}
    aligned = sorted(set(futures_map) & set(spot_map))
    futures = [one_bar(futures_map[minute]) for minute in aligned]
    spot = [one_bar(spot_map[minute]) for minute in aligned]
    atr = rolling_atr(futures, atr_minutes)

    data_manifest = json.loads((prepared_root / "data_manifest.json").read_text(encoding="utf-8"))
    evaluation_start_ns = int(data_manifest["evaluation_start_ns"])
    evaluation_end_ns = int(data_manifest["evaluation_end_ns"])

    signals: list[dict[str, Any]] = []
    no_trade_reasons: dict[str, int] = {}
    state_counts: dict[str, int] = {}
    event_counts = {"BUY_ABSORBED": 0, "SELL_ABSORBED": 0}
    gross_rrs: list[float] = []
    cooldown_until = 0

    def count(mapping: dict[str, int], key: str) -> None:
        mapping[key] = mapping.get(key, 0) + 1

    warmup = max(baseline_minutes + event_minutes, external_range_minutes, atr_minutes)
    for start in range(warmup, len(futures) - event_minutes):
        if start < cooldown_until:
            continue
        event = make_flow_window(futures, start, event_minutes)
        spot_event = make_flow_window(spot, start, event_minutes)
        if event is None or spot_event is None or event.direction == 0:
            continue
        event_end_ns = futures[event.end_index - 1].end_minute * NS_PER_MINUTE
        if not evaluation_start_ns <= event_end_ns < evaluation_end_ns:
            continue

        baseline: list[FlowWindow] = []
        baseline_start = max(0, start - baseline_minutes)
        for cursor in range(baseline_start, start - event_minutes + 1):
            item = make_flow_window(futures, cursor, event_minutes)
            if item is not None and item.direction != 0 and item.gross_notional > 0.0:
                baseline.append(item)
        if len(baseline) < 60:
            count(no_trade_reasons, "INSUFFICIENT_HORIZON_MATCHED_BASELINE")
            continue
        flow_q75 = _quantile([item.absolute_flow for item in baseline], 0.75)
        gross_q50 = _quantile([item.gross_notional for item in baseline], 0.50)
        response_q25 = _quantile([item.response_score for item in baseline], 0.25)
        if event.absolute_flow < flow_q75 or event.gross_notional < gross_q50:
            continue
        if event.response_score > response_q25:
            continue

        prior = futures[start - external_range_minutes : start]
        prior_spot = spot[start - external_range_minutes : start]
        if len(prior) != external_range_minutes or len(prior_spot) != external_range_minutes:
            continue
        prior_high = max(item.high for item in prior)
        prior_low = min(item.low for item in prior)
        prior_midpoint = (prior_low + prior_high) / 2.0
        if event.direction > 0:
            external_absorption = event.high > prior_high and event.close < prior_high
            spot_accepted = spot_event.close >= max(item.high for item in prior_spot)
            event_counts["BUY_ABSORBED"] += int(external_absorption)
        else:
            external_absorption = event.low < prior_low and event.close > prior_low
            spot_accepted = spot_event.close <= min(item.low for item in prior_spot)
            event_counts["SELL_ABSORBED"] += int(external_absorption)
        if not external_absorption:
            count(no_trade_reasons, "INEFFICIENT_FLOW_WITHOUT_EXTERNAL_PROBE_RECLAIM")
            continue
        if spot_accepted:
            count(no_trade_reasons, "SPOT_ACCEPTED_EXTERNAL_BREAK")
            continue
        spot_flow = (
            spot_event.signed_notional / spot_event.gross_notional
            if spot_event.gross_notional > 0.0
            else 0.0
        )
        if event.direction * spot_flow >= event.absolute_flow:
            count(no_trade_reasons, "SPOT_FLOW_CONFIRMED_AGGRESSOR")
            continue

        reversal_direction = -event.direction
        confirmation_index, confirmation = find_absorption_confirmation(
            futures,
            event=event,
            reversal_direction=reversal_direction,
            expiry_minutes=confirmation_expiry_minutes,
        )
        if confirmation_index is None or confirmation is None:
            count(no_trade_reasons, "ABSORPTION_REVERSAL_CONFIRMATION_UNRESOLVED")
            continue
        at = atr.get(confirmation.end_minute)
        if at is None or not math.isfinite(at) or at <= 0.0:
            count(no_trade_reasons, "MISSING_CAUSAL_ATR")
            continue
        entry_reference = confirmation.close
        target = prior_high if reversal_direction > 0 else prior_low
        if reversal_direction * (target - entry_reference) <= 0.0:
            count(no_trade_reasons, "OPPOSITE_EXTERNAL_TARGET_NOT_AHEAD")
            continue
        event_extreme = event.low if reversal_direction > 0 else event.high
        stop = event_extreme - reversal_direction * stop_buffer_atr * at
        risk_distance = reversal_direction * (entry_reference - stop)
        reward_distance = reversal_direction * (target - entry_reference)
        if risk_distance <= 0.0 or not math.isfinite(stop):
            count(no_trade_reasons, "NON_EXECUTABLE_ABSORPTION_INVALIDATION")
            continue
        gross_rr = reward_distance / risk_distance
        waypoint = (
            prior_midpoint
            if reversal_direction * (prior_midpoint - entry_reference) > 0.0
            and reversal_direction * (target - prior_midpoint) >= 0.0
            else None
        )

        event_start_ns = futures[event.start_index].start_minute * NS_PER_MINUTE
        confirm_ns = confirmation.end_minute * NS_PER_MINUTE
        oi_before = oi.get(event_start_ns)
        oi_after = oi.get(confirm_ns)
        oi_change_bp: float | None = None
        if oi_before is not None and oi_after is not None and oi_before > 0.0:
            oi_change_bp = (oi_after / oi_before - 1.0) * 10_000.0
        state = (
            OI_EXPANSION_ABSORPTION_REVERSAL
            if oi_change_bp is not None and oi_change_bp >= 0.0
            else OI_CONTRACTION_ABSORPTION_REVERSAL
        )
        suffix = sha256(
            f"{confirm_ns}|{reversal_direction}|{event.signed_notional:.12g}|{event.response_score:.12g}".encode()
        ).hexdigest()[:16]
        details = {
            "scenario_kind": state,
            "entry_kind": "REVERSAL",
            "entry_sequence": [
                "ROLLING_4H_HORIZON_MATCHED_FLOW_BASELINE",
                "EXTREME_AGGRESSIVE_FLOW",
                "LOW_PRICE_RESPONSE",
                "60M_EXTERNAL_PROBE_AND_RECLAIM",
                "SPOT_NON_ACCEPTANCE",
                "EVENT_MIDPOINT_REVERSAL_WITH_OPPOSITE_FLOW",
                "NEXT_NATIVE_QUOTE_ENTRY",
            ],
            "event_minutes": event_minutes,
            "event_start_time_ns": event_start_ns,
            "event_end_time_ns": event_end_ns,
            "aggressor_direction": event.direction,
            "event_open": event.open,
            "event_high": event.high,
            "event_low": event.low,
            "event_close": event.close,
            "event_gross_notional": event.gross_notional,
            "event_signed_notional": event.signed_notional,
            "event_absolute_flow": event.absolute_flow,
            "event_directional_progress_bp": event.directional_progress_bp,
            "event_response_score": event.response_score,
            "spot_event_flow": spot_flow,
            "flow_q75": flow_q75,
            "gross_q50": gross_q50,
            "response_q25": response_q25,
            "baseline_windows": len(baseline),
            "prior_range_low": prior_low,
            "prior_range_high": prior_high,
            "prior_range_midpoint": prior_midpoint,
            "confirmation_end_time_ns": confirm_ns,
            "confirmation_wait_minutes": confirmation_index - event.end_index + 1,
            "confirmation_open": confirmation.open,
            "confirmation_high": confirmation.high,
            "confirmation_low": confirmation.low,
            "confirmation_close": confirmation.close,
            "confirmation_flow": confirmation.flow,
            "oi_change_event_to_confirmation_bp": oi_change_bp,
            "stop_anchor": event_extreme,
            "stop_buffer_atr": stop_buffer_atr,
            "structural_target": target,
            "structural_waypoint": waypoint,
            "gross_structural_rr_at_confirmation": gross_rr,
        }
        signal: dict[str, Any] = {
            "scenario_id": f"NT-LVCFR-V25-{state}-{suffix}",
            "scenario_kind": state,
            "entry_kind": "REVERSAL",
            "confirm_time_ns": confirm_ns,
            "eligible_time_ns": confirm_ns,
            "direction": reversal_direction,
            "initial_stop": stop,
            "atr": at,
            "first_start_time_ns": event_start_ns,
            "first_end_time_ns": event_end_ns,
            "structural_target": target,
            "target_mode": "STRUCTURAL_LIQUIDITY_OBJECTIVE",
            "disable_rapid_failure_reversal": True,
            "details": details,
        }
        if waypoint is not None:
            signal["structural_protection_trigger"] = waypoint
        signals.append(signal)
        count(state_counts, state)
        gross_rrs.append(gross_rr)
        cooldown_until = confirmation_index + 1

    output_signals.parent.mkdir(parents=True, exist_ok=True)
    output_signals.write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ordered_rrs = sorted(gross_rrs)
    evaluation_days = (evaluation_end_ns - evaluation_start_ns) / (86_400 * 1_000_000_000)
    manifest = {
        "candidate": "candidate-03-nt-lvcfr-v25-external-absorption-reversal",
        "engine_status": "independent_causal_schedule_only_no_backtest",
        "derived_signal_count": len(signals),
        "evaluation_days": evaluation_days,
        "signals_per_day": len(signals) / evaluation_days if evaluation_days > 0.0 else 0.0,
        "output_state_counts": dict(sorted(state_counts.items())),
        "external_absorption_event_counts": event_counts,
        "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "event_minutes": event_minutes,
        "baseline_minutes": baseline_minutes,
        "external_range_minutes": external_range_minutes,
        "confirmation_expiry_minutes": confirmation_expiry_minutes,
        "atr_minutes": atr_minutes,
        "stop_buffer_atr": stop_buffer_atr,
        "minimum_gross_structural_rr": min(gross_rrs) if gross_rrs else None,
        "median_gross_structural_rr": ordered_rrs[len(ordered_rrs) // 2] if ordered_rrs else None,
        "selection_policy": (
            "rolling causal quartiles of horizon-matched flow/response; external liquidity reclaim; "
            "no return-fit search; native structural target cost gate required"
        ),
        "output_signals": str(output_signals),
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return signals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-signals", type=Path)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--event-minutes", type=int, default=3)
    parser.add_argument("--baseline-minutes", type=int, default=240)
    parser.add_argument("--external-range-minutes", type=int, default=60)
    parser.add_argument("--confirmation-expiry-minutes", type=int, default=3)
    parser.add_argument("--atr-minutes", type=int, default=20)
    parser.add_argument("--stop-buffer-atr", type=float, default=0.20)
    args = parser.parse_args()
    prepared = args.prepared_root.resolve()
    output = (args.output_signals or (prepared / "signals.json")).resolve()
    signals = derive_v25(
        prepared_root=prepared,
        output_signals=output,
        output_manifest=args.output_manifest.resolve(),
        event_minutes=args.event_minutes,
        baseline_minutes=args.baseline_minutes,
        external_range_minutes=args.external_range_minutes,
        confirmation_expiry_minutes=args.confirmation_expiry_minutes,
        atr_minutes=args.atr_minutes,
        stop_buffer_atr=args.stop_buffer_atr,
    )
    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v25-external-absorption-reversal",
                "signals": len(signals),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
