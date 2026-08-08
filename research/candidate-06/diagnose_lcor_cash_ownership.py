#!/usr/bin/env python3
"""Diagnose LCOR cash-ownership resets without changing trading decisions.

The diagnostic replays the exact prior-only primitive stream used by the
Nautilus strategy and asks one narrow causal question: did a single cash-market
boundary close terminate an episode before renewed initiative could occur, and
would requiring adverse cash order flow to confirm ownership loss change only
that state classification?
"""

from __future__ import annotations

import argparse
from collections import deque
from datetime import date
import json
import os
from pathlib import Path
from typing import Any

from cross_venue_data import load_spot_week
from futures_metrics_data import load_week as load_metrics_week
from market_data import load_week as load_bar_week
from nautilus_runner import frame_to_observations
from primitives import CausalPrimitiveDetector

MINUTE_NS = 60_000_000_000
START_REASON = (
    "PERPETUAL_LED_EXTERNAL_SWEEP_WITH_EXTREME_OI_CONTRACTION_"
    "AND_INITIAL_SPOT_NON_ACCEPTANCE"
)
PULLBACK_REASON = "FIRST_OPPOSING_FLOW_PULLBACK_HELD_CASH_OWNED_BOUNDARY"
RESET_REASON = "CASH_AUCTION_LOST_OWNERSHIP_BEFORE_ENTRY"


def _load_events(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _spot_atr_before(observations: dict[int, Any], lookback: int) -> dict[int, float | None]:
    result: dict[int, float | None] = {}
    true_ranges: deque[float] = deque(maxlen=240)
    prior_close: float | None = None
    for ts_ns in sorted(observations):
        bar = observations[ts_ns]
        result[ts_ns] = (
            sum(list(true_ranges)[-lookback:]) / lookback
            if len(true_ranges) >= lookback
            else None
        )
        previous = prior_close if prior_close is not None else bar.open
        true_ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous),
                abs(bar.low - previous),
            ),
        )
        prior_close = bar.close
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("artifacts/candidate-06/lcor-w2-first"),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    variant = "lcor_full_cash_ownership_relay"
    config_path = root / "configs" / f"{variant}-week-2.json"
    events_path = root / "runs" / variant / "week-2" / "scenario_events.jsonl"
    output = root / "diagnostics"
    output.mkdir(parents=True, exist_ok=True)
    if not config_path.exists() or not events_path.exists():
        raise FileNotFoundError("LCOR W2 matrix evidence must exist before diagnosis")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    logic = dict(config["logic"])
    weeks = [date.fromisoformat(value) for value in config["validation"]["frozen_week_starts_utc"]]
    week_start = weeks[1]
    data_root = Path(os.getenv("SMC4_RESEARCH_DATA", ".research-data/candidate-06")).resolve()
    perpetual = load_bar_week(config["validation"]["symbol"], week_start, data_root)
    spot = load_spot_week(config["validation"]["symbol"], week_start, data_root)
    metrics = load_metrics_week(config["validation"]["symbol"], week_start, data_root)
    perp_observations = frame_to_observations(perpetual.frame)
    spot_observations = frame_to_observations(spot.frame)

    detector = CausalPrimitiveDetector(logic)
    snapshots = {
        ts_ns: detector.observe(perp_observations[ts_ns])
        for ts_ns in sorted(perp_observations)
    }
    spot_atr = _spot_atr_before(
        spot_observations,
        int(logic.get("ciot_spot_atr_bars", 20)),
    )
    events = _load_events(events_path)
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_scenario.setdefault(str(event["scenario_id"]), []).append(event)

    episodes: list[dict[str, Any]] = []
    full_windows: list[dict[str, Any]] = []
    for scenario_id, scenario_events in by_scenario.items():
        starts = [item for item in scenario_events if item.get("reason_code") == START_REASON]
        resets = [item for item in scenario_events if item.get("reason_code") == RESET_REASON]
        if not starts or not resets:
            continue
        start = starts[0]
        reset = resets[-1]
        pullbacks = [item for item in scenario_events if item.get("reason_code") == PULLBACK_REASON]
        pullback = pullbacks[-1] if pullbacks else None
        details = dict(start.get("details", {}))
        direction = str(details["direction"])
        start_ts = int(start["observed_time_ns"])
        reset_ts = int(reset["observed_time_ns"])
        start_snapshot = snapshots[start_ts]
        event_atr = float(start_snapshot.atr)
        boundary = float(details["spot_boundary"])
        baseline_oi = float(details["baseline_open_interest"])
        event_oi = float(details["event_open_interest"])
        removed = max(baseline_oi - event_oi, 0.0)
        retention = float(logic.get("lcor_forced_removal_retention_fraction", 0.35))
        ceiling = baseline_oi - removed * retention
        episode_bars = int(logic.get("lcor_episode_bars", 30))
        floor = float(logic.get("lcor_response_flow_ratio", 0.05))
        location = float(logic.get("lcor_response_close_location", 0.58))
        extension = float(logic.get("lcor_extension_atr", 0.05)) * event_atr
        pullback_ts = int(pullback["observed_time_ns"]) if pullback else None
        pullback_details = dict(pullback.get("details", {})) if pullback else {}
        pullback_high = float(pullback_details["pullback_high"]) if pullback else None
        pullback_low = float(pullback_details["pullback_low"]) if pullback else None

        inventory_confirmed = False
        flow_guard_active = True
        first_flow_loss_ts: int | None = None
        first_inventory_loss_ts: int | None = None
        first_resumption_ts: int | None = None
        rows: list[dict[str, Any]] = []
        start_index = int(start_snapshot.index)
        for ts_ns in sorted(snapshots):
            snapshot = snapshots[ts_ns]
            if snapshot.index <= start_index:
                continue
            if snapshot.index - start_index > episode_bars:
                break
            spot_bar = spot_observations[ts_ns]
            current_spot_atr = spot_atr[ts_ns]
            tolerance = (
                float(logic.get("lcor_spot_hold_tolerance_atr", 0.03))
                * float(current_spot_atr)
                if current_spot_atr is not None
                else 0.0
            )
            if direction == "LONG":
                strict_loss = spot_bar.close < boundary - tolerance
                adverse_cash_flow = spot_bar.flow_ratio <= -floor
                resumed = bool(
                    pullback_ts is not None
                    and ts_ns > pullback_ts
                    and snapshot.observation.close >= float(pullback_high) + extension
                    and snapshot.flow_ratio >= floor
                    and snapshot.close_location >= location
                )
            else:
                strict_loss = spot_bar.close > boundary + tolerance
                adverse_cash_flow = spot_bar.flow_ratio >= floor
                resumed = bool(
                    pullback_ts is not None
                    and ts_ns > pullback_ts
                    and snapshot.observation.close <= float(pullback_low) - extension
                    and snapshot.flow_ratio <= -floor
                    and snapshot.close_location <= 1.0 - location
                )
            flow_confirmed_loss = strict_loss and adverse_cash_flow

            metric = metrics.observations.get(ts_ns)
            inventory_lost = False
            if metric is not None and ts_ns > start_ts:
                if metric.open_interest <= ceiling:
                    inventory_confirmed = True
                elif inventory_confirmed:
                    inventory_lost = True
                    if first_inventory_loss_ts is None:
                        first_inventory_loss_ts = ts_ns

            if flow_guard_active and flow_confirmed_loss:
                flow_guard_active = False
                first_flow_loss_ts = ts_ns
            if (
                flow_guard_active
                and not inventory_lost
                and resumed
                and first_resumption_ts is None
            ):
                first_resumption_ts = ts_ns

            if reset_ts - 5 * MINUTE_NS <= ts_ns <= reset_ts + 20 * MINUTE_NS:
                rows.append(
                    {
                        "ts_ns": ts_ns,
                        "perp_open": snapshot.observation.open,
                        "perp_high": snapshot.observation.high,
                        "perp_low": snapshot.observation.low,
                        "perp_close": snapshot.observation.close,
                        "perp_flow_ratio": snapshot.flow_ratio,
                        "perp_close_location": snapshot.close_location,
                        "spot_open": spot_bar.open,
                        "spot_high": spot_bar.high,
                        "spot_low": spot_bar.low,
                        "spot_close": spot_bar.close,
                        "spot_flow_ratio": spot_bar.flow_ratio,
                        "spot_atr_prior_only": current_spot_atr,
                        "spot_boundary": boundary,
                        "strict_cash_ownership_loss": strict_loss,
                        "adverse_cash_flow": adverse_cash_flow,
                        "flow_confirmed_cash_ownership_loss": flow_confirmed_loss,
                        "renewed_initiative": resumed,
                        "metric_open_interest": metric.open_interest if metric is not None else None,
                        "inventory_confirmed": inventory_confirmed,
                        "inventory_lost": inventory_lost,
                    },
                )

        reset_bar = spot_observations[reset_ts]
        reset_atr = spot_atr[reset_ts]
        reset_tolerance = (
            float(logic.get("lcor_spot_hold_tolerance_atr", 0.03)) * float(reset_atr)
            if reset_atr is not None
            else 0.0
        )
        distance = (
            boundary - reset_bar.close
            if direction == "LONG"
            else reset_bar.close - boundary
        )
        episodes.append(
            {
                "scenario_id": scenario_id,
                "direction": direction,
                "start_ts_ns": start_ts,
                "pullback_ts_ns": pullback_ts,
                "strict_reset_ts_ns": reset_ts,
                "spot_boundary": boundary,
                "strict_reset_spot_close": reset_bar.close,
                "strict_reset_spot_flow_ratio": reset_bar.flow_ratio,
                "strict_reset_distance": distance,
                "strict_reset_distance_atr": (
                    distance / float(reset_atr) if reset_atr not in (None, 0.0) else None
                ),
                "strict_reset_tolerance": reset_tolerance,
                "first_flow_confirmed_loss_ts_ns": first_flow_loss_ts,
                "first_inventory_loss_ts_ns": first_inventory_loss_ts,
                "first_resumption_ts_under_flow_confirmed_ownership_ns": first_resumption_ts,
                "flow_confirmed_ownership_would_reach_resumption": first_resumption_ts is not None,
            },
        )
        full_windows.append({"scenario_id": scenario_id, "rows": rows})

    payload = {
        "diagnostic_only": True,
        "decision_rule_unchanged": True,
        "question": (
            "Does a price-only cash-boundary breach reset LCOR before renewed initiative, "
            "while a price-plus-adverse-cash-flow ownership-loss definition would preserve "
            "the same causal episode long enough to observe resumption?"
        ),
        "episodes": episodes,
        "windows": full_windows,
    }
    (output / "cash_ownership_reset_windows.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["episodes"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
