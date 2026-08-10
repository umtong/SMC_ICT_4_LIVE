#!/usr/bin/env python3
"""Diagnose control-v8 versus role-separated-v9 residual episodes.

This is intentionally not a pass/fail gate. It preserves every frozen state,
strictly-later observation, confirmation, no-trade reason, order, position and
account result so that a change is judged by the causal transactions it alters,
not only by headline return.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Iterable


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
STATE_EVENT = "CROSS_SECTIONAL_RESIDUAL_STATE_FROZEN"
OBSERVATION_EVENT = "RESIDUAL_STATE_LATER_OBSERVATION"
CONFIRMATION_EVENT = "STRICTLY_LATER_RESIDUAL_CONVERGENCE_CONFIRMED"
TERMINAL_EVENTS = {
    "RESIDUAL_STATE_EXPIRED",
    "RESIDUAL_OBJECTIVE_CONSUMED_WITHOUT_ENTRY",
    "V8_ENTRY_GEOMETRY_REJECTED",
    "V8_NO_TRADEABLE_LIQUIDITY_OBJECTIVE",
    "V8_FOK_LIQUIDITY_BRACKET_SUBMITTED",
}
CONDITIONS = (
    "residual_contraction",
    "state_price_cross",
    "relative_return_turn",
    "directional_candle",
    "flow_alignment",
    "depth_alignment",
)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists() or path.stat().st_size == 0:
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def diagnostics_total(metrics: dict[str, Any], key: str) -> int:
    return sum(
        int(item.get(key, 0) or 0)
        for item in metrics.get("strategy_diagnostics", {}).values()
    )


def iter_events(result: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    for symbol in SYMBOLS:
        path = result / "symbols" / symbol / "scenario_events.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                yield symbol, event


def load_closed_scenarios(result: Path) -> dict[str, dict[str, Any]]:
    rows = read_json(result / "closed_scenarios_all.json", [])
    if not isinstance(rows, list):
        return {}
    answer: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        scenario_id = str(row.get("scenario_id", ""))
        if scenario_id:
            answer[scenario_id] = row
    return answer


def condition_flags(
    observation: dict[str, Any],
    *,
    side: int,
    state_close: float,
    initial_residual: float,
) -> dict[str, bool]:
    residual = finite(observation.get("residual"))
    close = finite(observation.get("close"))
    open_ = finite(observation.get("open"))
    own1 = finite(observation.get("own_normalized_1m"))
    peer1 = finite(observation.get("peer_normalized_1m"))
    flow = finite(observation.get("flow_60s"))
    depth = finite(observation.get("depth_imbalance_1"))
    return {
        "residual_contraction": bool(
            residual is not None
            and residual != 0.0
            and residual * initial_residual > 0.0
            and abs(residual) < abs(initial_residual)
        ),
        "state_price_cross": bool(
            close is not None and side * (close - state_close) > 0.0
        ),
        "relative_return_turn": bool(
            own1 is not None and peer1 is not None and side * (own1 - peer1) > 0.0
        ),
        "directional_candle": bool(
            close is not None and open_ is not None and side * (close - open_) > 0.0
        ),
        "flow_alignment": bool(flow is not None and side * flow > 0.0),
        "depth_alignment": bool(depth is not None and side * depth > 0.0),
    }


@dataclass
class Episode:
    period: str
    variant: str
    symbol: str
    scenario_id: str
    state_event: dict[str, Any]
    observations: list[dict[str, Any]] = field(default_factory=list)
    confirmation_event: dict[str, Any] | None = None
    terminal_event: dict[str, Any] | None = None
    closed_scenario: dict[str, Any] | None = None

    def row(self) -> dict[str, Any]:
        details = self.state_event.get("details", {}) or {}
        initial = finite(details.get("v8_initial_residual"))
        if initial is None:
            initial = finite(details.get("residual"))
        initial = initial or 0.0
        side = int(details.get("side") or (-1 if initial > 0.0 else 1))
        state_close = finite(details.get("v8_state_close"))
        if state_close is None:
            state_close = finite(self.state_event.get("reference_price")) or 0.0
        state_ts = int(
            details.get("v8_state_ts")
            or self.state_event.get("event_time_ns")
            or 0
        )
        z = finite(details.get("residual_z"))
        oi = finite(details.get("oi_change_15m"))
        micro = details.get("candidate16_v9_state_bar_microstructure", {}) or {}

        flags_by_bar: list[dict[str, bool]] = []
        min_abs_ratio: float | None = None
        max_favorable_bps = 0.0
        max_adverse_bps = 0.0
        first_all_ts: int | None = None
        best_score = 0
        for observation in self.observations:
            flags = condition_flags(
                observation,
                side=side,
                state_close=state_close,
                initial_residual=initial,
            )
            flags_by_bar.append(flags)
            score = sum(flags.values())
            best_score = max(best_score, score)
            if all(flags.values()) and first_all_ts is None:
                first_all_ts = int(observation.get("ts_event") or 0)

            residual = finite(observation.get("residual"))
            if residual is not None and initial != 0.0:
                ratio = abs(residual) / abs(initial)
                min_abs_ratio = ratio if min_abs_ratio is None else min(
                    min_abs_ratio,
                    ratio,
                )
            high = finite(observation.get("high"))
            low = finite(observation.get("low"))
            if state_close > 0.0 and high is not None and low is not None:
                if side > 0:
                    max_favorable_bps = max(
                        max_favorable_bps,
                        (high / state_close - 1.0) * 10_000.0,
                    )
                    max_adverse_bps = max(
                        max_adverse_bps,
                        (1.0 - low / state_close) * 10_000.0,
                    )
                else:
                    max_favorable_bps = max(
                        max_favorable_bps,
                        (1.0 - low / state_close) * 10_000.0,
                    )
                    max_adverse_bps = max(
                        max_adverse_bps,
                        (high / state_close - 1.0) * 10_000.0,
                    )

        any_condition = {
            name: any(flags[name] for flags in flags_by_bar)
            for name in CONDITIONS
        }
        all_but = {
            f"any_all_but_{name}": any(
                all(flags[other] for other in CONDITIONS if other != name)
                for flags in flags_by_bar
            )
            for name in CONDITIONS
        }
        terminal_type = (
            str(self.terminal_event.get("event_type"))
            if self.terminal_event
            else "OPEN_OR_NO_TERMINAL_EVENT"
        )
        terminal_reason = (
            str(self.terminal_event.get("reason_code"))
            if self.terminal_event
            else ""
        )
        closed = self.closed_scenario or {}
        pnl = finite(
            closed.get("realized_pnl")
            if "realized_pnl" in closed
            else closed.get("realized_pnl_usdt")
        )
        return {
            "period": self.period,
            "variant": self.variant,
            "symbol": self.symbol,
            "scenario_id": self.scenario_id,
            "side": side,
            "state_ts_ns": state_ts,
            "state_close": state_close,
            "initial_residual": initial,
            "residual_z": z,
            "oi_change_15m": oi,
            "state_legacy_microstructure_pass": bool(
                micro.get("legacy_v52_gate_pass", False)
            ),
            "state_flow_aligned": bool(micro.get("flow_aligned", False)),
            "state_tail_acceleration_aligned": bool(
                micro.get("tail_acceleration_aligned", False)
            ),
            "state_depth_aligned": bool(micro.get("depth_aligned", False)),
            "state_efficiency_pass": bool(micro.get("efficiency_pass", False)),
            "state_notional_burst_pass": bool(
                micro.get("notional_burst_pass", False)
            ),
            "later_observations": len(self.observations),
            "min_abs_residual_ratio": min_abs_ratio,
            "max_favorable_bps_from_state": max_favorable_bps,
            "max_adverse_bps_from_state": max_adverse_bps,
            "best_confirmation_condition_count": best_score,
            "confirmed": self.confirmation_event is not None,
            "first_all_conditions_ts_ns": first_all_ts,
            "terminal_event": terminal_type,
            "terminal_reason": terminal_reason,
            "closed_trade_record": bool(closed),
            "realized_pnl_usdt": pnl,
            **{f"any_{name}": value for name, value in any_condition.items()},
            **all_but,
        }


def collect_episodes(period: str, variant: str, result: Path) -> list[Episode]:
    episodes: dict[str, Episode] = {}
    closed = load_closed_scenarios(result)
    for symbol, event in iter_events(result):
        scenario_id = str(event.get("scenario_id", ""))
        event_type = str(event.get("event_type", ""))
        if (
            event_type == STATE_EVENT
            and not scenario_id.startswith(("v52-", "v8-", "v9-"))
        ):
            continue
        episode = episodes.get(scenario_id)
        if event_type == STATE_EVENT:
            episode = Episode(
                period=period,
                variant=variant,
                symbol=symbol,
                scenario_id=scenario_id,
                state_event=event,
            )
            episodes[scenario_id] = episode
            continue
        if episode is None:
            continue
        if event_type == OBSERVATION_EVENT:
            details = event.get("details", {}) or {}
            observation = details.get("v8_current_observation")
            if isinstance(observation, dict):
                episode.observations.append(observation)
        elif event_type == CONFIRMATION_EVENT:
            episode.confirmation_event = event
        elif event_type in TERMINAL_EVENTS:
            episode.terminal_event = event
    for scenario_id, episode in episodes.items():
        episode.closed_scenario = closed.get(scenario_id)
    return list(episodes.values())


def run_summary(period: str, variant: str, result: Path) -> dict[str, Any]:
    metrics = read_json(result / "metrics.json", {})
    diagnostics = metrics.get("strategy_diagnostics", {})
    integrity = bool(metrics.get("integrity_pass"))
    non_residual = sum(
        int(item.get("candidate16_v8_non_residual_submission_attempts", 0) or 0)
        + int(item.get("candidate16_v9_non_residual_submission_attempts", 0) or 0)
        for item in diagnostics.values()
    )
    same_timestamp = diagnostics_total(metrics, "v52_same_timestamp_peer_uses")
    return {
        "period": period,
        "variant": variant,
        "integrity_pass": integrity,
        "same_timestamp_peer_uses": same_timestamp,
        "non_residual_submission_attempts": non_residual,
        "peer_context_ready": diagnostics_total(metrics, "v52_peer_context_ready"),
        "robust_extremes": diagnostics_total(metrics, "v52_extremes"),
        "residual_inflections": diagnostics_total(metrics, "v52_inflections"),
        "oi_non_expansion_pass": diagnostics_total(metrics, "v52_oi_contraction_pass"),
        "legacy_state_microstructure_pass": diagnostics_total(
            metrics,
            "v52_flow_depth_pass",
        ),
        "legacy_v52_setups": diagnostics_total(metrics, "v52_setups"),
        "v8_states_frozen": diagnostics_total(
            metrics,
            "candidate16_v8_states_frozen",
        ),
        "v9_states_frozen": diagnostics_total(
            metrics,
            "candidate16_v9_states_frozen",
        ),
        "later_observations": diagnostics_total(
            metrics,
            "candidate16_v8_later_observations",
        ),
        "residual_contractions": diagnostics_total(
            metrics,
            "candidate16_v8_residual_contractions",
        ),
        "residual_neutralized": diagnostics_total(
            metrics,
            "candidate16_v8_residual_neutralized",
        ),
        "states_expired": diagnostics_total(
            metrics,
            "candidate16_v8_states_expired",
        ),
        "later_confirmations": diagnostics_total(
            metrics,
            "candidate16_v8_later_confirmations",
        ),
        "no_natural_target": diagnostics_total(
            metrics,
            "candidate16_v8_no_natural_target",
        ),
        "geometry_rejected": diagnostics_total(
            metrics,
            "candidate16_v8_geometry_rejected",
        ),
        "entry_submissions": diagnostics_total(metrics, "entry_submissions"),
        "trades": int(metrics.get("trades", 0) or 0),
        "wins": int(metrics.get("wins", 0) or 0),
        "losses": int(metrics.get("losses", 0) or 0),
        "win_rate": float(metrics.get("win_rate", 0.0) or 0.0),
        "total_return": float(metrics.get("total_return", 0.0) or 0.0),
        "geometric_daily_growth": float(
            metrics.get("geometric_daily_growth", 0.0) or 0.0,
        ),
        "max_drawdown": float(metrics.get("max_drawdown", 0.0) or 0.0),
        "ending_nav": float(metrics.get("ending_nav", 0.0) or 0.0),
        "gross_profit": float(metrics.get("gross_profit", 0.0) or 0.0),
        "gross_loss": float(metrics.get("gross_loss", 0.0) or 0.0),
    }


def aggregate_episode_diagnosis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    variant = [row for row in rows if row["variant"] == "variant"]
    controls = [row for row in rows if row["variant"] == "control"]

    def count(predicate) -> int:
        return sum(1 for row in variant if predicate(row))

    terminal_counts: dict[str, int] = {}
    for row in variant:
        terminal_counts[row["terminal_event"]] = (
            terminal_counts.get(row["terminal_event"], 0) + 1
        )
    condition_counts = {
        name: count(lambda row, name=name: bool(row[f"any_{name}"]))
        for name in CONDITIONS
    }
    all_but_counts = {
        name: count(lambda row, name=name: bool(row[f"any_all_but_{name}"]))
        for name in CONDITIONS
    }
    pnl_values = [
        float(row["realized_pnl_usdt"])
        for row in variant
        if row["realized_pnl_usdt"] is not None
    ]
    diagnosis: list[str] = []
    if not variant:
        diagnosis.append("ROLE_SEPARATION_NOT_EXERCISED_NO_FROZEN_STATE")
    else:
        confirmed = count(lambda row: bool(row["confirmed"]))
        traded = count(lambda row: bool(row["closed_trade_record"]))
        if confirmed == 0:
            diagnosis.append("STATE_REACHED_BUT_LATER_TRANSITION_NOT_CONFIRMED")
        elif traded == 0:
            diagnosis.append("LATER_TRANSITION_REACHED_BUT_NO_COMPLETED_TRADE")
        else:
            diagnosis.append("TRADE_ECONOMICS_OBSERVED_REQUIRES_TRANSACTION_REVIEW")
        if condition_counts.get("depth_alignment", 0) < max(
            condition_counts.get("residual_contraction", 0),
            condition_counts.get("state_price_cross", 0),
        ):
            diagnosis.append("DEPTH_ALIGNMENT_IS_RELATIVELY_SCARCE")
        if condition_counts.get("flow_alignment", 0) < max(
            condition_counts.get("residual_contraction", 0),
            condition_counts.get("state_price_cross", 0),
        ):
            diagnosis.append("FLOW_ALIGNMENT_IS_RELATIVELY_SCARCE")
    return {
        "control_episode_count": len(controls),
        "variant_episode_count": len(variant),
        "variant_confirmed_episodes": count(lambda row: bool(row["confirmed"])),
        "variant_completed_trade_records": count(
            lambda row: bool(row["closed_trade_record"]),
        ),
        "variant_positive_trade_records": sum(value > 0.0 for value in pnl_values),
        "variant_negative_trade_records": sum(value < 0.0 for value in pnl_values),
        "variant_realized_pnl_usdt": sum(pnl_values),
        "variant_legacy_microstructure_subset": count(
            lambda row: bool(row["state_legacy_microstructure_pass"]),
        ),
        "variant_terminal_event_counts": terminal_counts,
        "variant_any_condition_counts": condition_counts,
        "variant_any_all_but_condition_counts": all_but_counts,
        "diagnosis": diagnosis,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(
    path: Path,
    summaries: list[dict[str, Any]],
    diagnosis: dict[str, Any],
) -> None:
    lines = [
        "# Candidate 16 v9 role-separation diagnosis",
        "",
        "This report is transaction- and no-trade-oriented; no headline metric is used as a binary strategy gate.",
        "",
        "## Account results",
        "",
        "| Period | Variant | Integrity | States | Later confirms | Entries | Trades | Return | Daily geo | Drawdown |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        states = row["v9_states_frozen"] or row["v8_states_frozen"]
        lines.append(
            "| {period} | {variant} | {integrity_pass} | {states} | {later_confirmations} | "
            "{entry_submissions} | {trades} | {total_return:.6f} | "
            "{geometric_daily_growth:.6f} | {max_drawdown:.6f} |".format(
                states=states,
                **row,
            ),
        )
    lines.extend(
        [
            "",
            "## Variant episode diagnosis",
            "",
            f"- Frozen episodes: {diagnosis['variant_episode_count']}",
            f"- Later confirmations: {diagnosis['variant_confirmed_episodes']}",
            f"- Completed trade records: {diagnosis['variant_completed_trade_records']}",
            f"- Realized PnL: {diagnosis['variant_realized_pnl_usdt']:.6f} USDT",
            f"- Legacy v52 microstructure subset: {diagnosis['variant_legacy_microstructure_subset']}",
            f"- Diagnosis: {', '.join(diagnosis['diagnosis']) or 'UNRESOLVED'}",
            "",
            "### Any condition reached within the later window",
            "",
            "| Condition | Episodes |",
            "|---|---:|",
        ],
    )
    for name, value in diagnosis["variant_any_condition_counts"].items():
        lines.append(f"| {name} | {value} |")
    lines.extend(
        [
            "",
            "### Terminal outcomes",
            "",
            "| Event | Episodes |",
            "|---|---:|",
        ],
    )
    for name, value in sorted(diagnosis["variant_terminal_event_counts"].items()):
        lines.append(f"| {name} | {value} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    summaries: list[dict[str, Any]] = []
    episode_objects: list[Episode] = []
    for period_dir in sorted(path for path in args.root.iterdir() if path.is_dir()):
        for variant in ("control", "variant"):
            result = period_dir / variant
            if not (result / "metrics.json").exists():
                continue
            summaries.append(run_summary(period_dir.name, variant, result))
            episode_objects.extend(
                collect_episodes(period_dir.name, variant, result),
            )

    summaries.sort(key=lambda row: (row["period"], row["variant"]))
    episode_rows = [episode.row() for episode in episode_objects]
    episode_rows.sort(
        key=lambda row: (
            row["period"],
            row["variant"],
            row["state_ts_ns"],
            row["symbol"],
        ),
    )
    diagnosis = aggregate_episode_diagnosis(episode_rows)
    integrity_failures = [
        {
            key: row[key]
            for key in (
                "period",
                "variant",
                "integrity_pass",
                "same_timestamp_peer_uses",
                "non_residual_submission_attempts",
            )
        }
        for row in summaries
        if (
            not row["integrity_pass"]
            or row["same_timestamp_peer_uses"] != 0
            or row["non_residual_submission_attempts"] != 0
        )
    ]
    payload = {
        "schema": "candidate-16-v9-role-separation-paired-diagnosis-v1",
        "experiment_role": "DEVELOPMENT_CAUSAL_DIAGNOSIS_NOT_HOLDOUT",
        "changed_component": (
            "STATE_BAR_FLOW_DEPTH_EFFICIENCY_BURST_MOVED_FROM_ADMISSION_TO_DIAGNOSTIC"
        ),
        "unchanged_components": [
            "ROBUST_RESIDUAL_THRESHOLD",
            "RESIDUAL_INFLECTION",
            "OI_NON_EXPANSION",
            "STRICTLY_LATER_V8_TRANSITION",
            "FOK_WORST_FILL_EXECUTION",
            "STATE_TO_CONFIRMATION_INVALIDATION",
            "PRE_EXISTING_LIQUIDITY_TARGET_AT_LEAST_ONE_NET_R",
            "THREE_PERCENT_CURRENT_NAV_RISK",
            "ONE_SHARED_ACCOUNT_GLOBAL_SLOT",
        ],
        "integrity_failures": integrity_failures,
        "account_summaries": summaries,
        "episode_diagnosis": diagnosis,
        "episode_count": len(episode_rows),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "paired_diagnosis.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    write_csv(args.output / "account_summaries.csv", summaries)
    write_csv(args.output / "residual_episodes.csv", episode_rows)
    write_markdown(args.output / "paired_diagnosis.md", summaries, diagnosis)
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=True))


if __name__ == "__main__":
    main()
