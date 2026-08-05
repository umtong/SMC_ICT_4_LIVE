#!/usr/bin/env python3
"""Diagnose every resting-pool structure break across flow regimes.

The production probe requires a 1.7-z opposite flow shock and therefore emits
almost no trades.  This diagnostic does not select a new threshold.  It records
the first causal internal-structure break after every valid repeated-pool sweep,
replays the unchanged next-minute entry/structural stop/opposing-pool target
with cost, and reports predeclared flow-strength and pool-quality regimes.  A
candidate can advance only if expectancy improves monotonically and transfers
across frozen weeks.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from core import CandidateConfig, Side  # noqa: E402
from data import load_interval, to_auction_bars  # noqa: E402
from resting_liquidity_pool_probe import (  # noqa: E402
    RestingLiquidityPoolDetector,
    SweepAttempt,
    aggregate_five_minute,
    week_segments,
)


RULES = (
    "structure-break-only",
    "directional-flow",
    "moderate-flow",
    "strong-flow",
    "three-touch-moderate",
    "aged-pools-moderate",
)


class DiagnosticDetector(RestingLiquidityPoolDetector):
    """Emit at the first directional CHoCH, retaining the original geometry."""

    def _confirm_attempt(self, *, bar: Any, state: Any) -> None:
        attempt: SweepAttempt | None = self.attempt
        if attempt is None:
            return
        if self.index > attempt.expiry_index:
            self.rejections["expired_attempt"] += 1
            self.attempt = None
            return
        if self.index <= attempt.started_index:
            return
        if state.flow_z is None or state.body_atr is None or state.volume_z is None:
            return
        if attempt.side is Side.LONG:
            attempt.sweep_extreme = min(attempt.sweep_extreme, bar.low)
            structure_break = (
                bar.close >= attempt.internal_break + 0.03 * attempt.atr
                and state.body_atr >= 0.25
                and state.flow_z >= 0.0
            )
        else:
            attempt.sweep_extreme = max(attempt.sweep_extreme, bar.high)
            structure_break = (
                bar.close <= attempt.internal_break - 0.03 * attempt.atr
                and state.body_atr <= -0.25
                and state.flow_z <= 0.0
            )
        if not structure_break:
            return
        plan = self._emit_plan(
            rule="two-touch-resting-pool",
            bar=bar,
            state=state,
            attempt=attempt,
        )
        if plan is not None:
            self.cooldown_until = self.index + 6
        self.attempt = None


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def path_row(
    *,
    pending: Any,
    bars: list[Any],
    index_by_ts: dict[int, int],
    cost: float,
) -> dict[str, Any]:
    plan = pending.plan
    signal_index = index_by_ts.get(plan.signal_time_ns)
    if signal_index is None or signal_index + 1 >= len(bars):
        return {
            "scenario_id": plan.scenario_id,
            "valid": False,
            "reason": "missing_next_bar",
        }
    entry_index = signal_index + 1
    entry_bar = bars[entry_index]
    entry = entry_bar.close
    stop = plan.stop_price
    target = plan.target_price
    geometry = stop < entry < target if plan.side is Side.LONG else target < entry < stop
    if not geometry:
        return {
            **asdict(plan),
            "side": plan.side.value,
            "response": plan.response.value,
            "valid": False,
            "reason": "invalid_delayed_geometry",
            "entry_time_ns": entry_bar.ts_event_ns,
            "entry": entry,
            "stop": stop,
            "target": target,
        }
    price_risk = abs(entry - stop)
    planned_loss = price_risk + entry * cost + stop * cost
    planned_gain = abs(target - entry) - entry * cost - target * cost
    price_risk_fraction = price_risk / planned_loss if planned_loss > 0.0 else 0.0
    net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
    final_index = min(entry_index + plan.max_hold_bars, len(bars) - 1)
    exit_price = bars[final_index].close
    exit_reason = "TIME"
    exit_index = final_index
    for index in range(entry_index + 1, final_index + 1):
        value = bars[index]
        if plan.side is Side.LONG:
            stop_hit = value.low <= stop
            target_hit = value.high >= target
        else:
            stop_hit = value.high >= stop
            target_hit = value.low <= target
        if stop_hit:
            exit_price = stop
            exit_reason = "STOP"
            exit_index = index
            break
        if target_hit:
            exit_price = target
            exit_reason = "TARGET"
            exit_index = index
            break
    gross = (exit_price - entry) * plan.side.sign
    net = gross - entry * cost - exit_price * cost
    return {
        **asdict(plan),
        "side": plan.side.value,
        "response": plan.response.value,
        "valid": True,
        "entry_time_ns": entry_bar.ts_event_ns,
        "entry": entry,
        "stop": stop,
        "target": target,
        "planned_loss_per_unit": planned_loss,
        "planned_gain_per_unit": planned_gain,
        "price_risk_fraction": price_risk_fraction,
        "net_reward_risk": net_rr,
        "exit_time_ns": bars[exit_index].ts_event_ns,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "bars_held": exit_index - entry_index,
        "realized_r": net / planned_loss if planned_loss > 0.0 else -1.0,
    }


def rule_mask(frame: pd.DataFrame, rule: str) -> pd.Series:
    flow = frame["confirmation_flow_z"]
    body = frame["confirmation_body_atr"]
    if rule == "structure-break-only":
        return (flow >= 0.0) & (body >= 0.25)
    if rule == "directional-flow":
        return (flow >= 0.50) & (body >= 0.30)
    if rule == "moderate-flow":
        return (flow >= 1.00) & (body >= 0.35)
    if rule == "strong-flow":
        return (flow >= 1.70) & (body >= 0.50)
    if rule == "three-touch-moderate":
        return (
            (frame["source_touches"] >= 3)
            & (flow >= 0.75)
            & (body >= 0.35)
        )
    if rule == "aged-pools-moderate":
        return (
            (frame["source_age_bars"] >= 24)
            & (frame["target_age_bars"] >= 24)
            & (flow >= 0.75)
            & (body >= 0.35)
        )
    raise ValueError(rule)


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    values = pd.to_numeric(
        frame.get("realized_r", pd.Series(dtype=float)),
        errors="coerce",
    ).dropna()
    profits = float(values[values > 0.0].sum())
    losses = abs(float(values[values < 0.0].sum()))
    return {
        "trades": int(len(values)),
        "sum_r": float(values.sum()),
        "mean_r": float(values.mean()) if len(values) else None,
        "win_rate": float((values > 0.0).mean()) if len(values) else None,
        "profit_factor": profits / losses if losses > 0.0 else None,
        "exit_counts": frame.get(
            "exit_reason",
            pd.Series(dtype=str),
        ).value_counts().to_dict(),
        "cost_dominated": (
            int((frame["price_risk_fraction"] < 0.65).sum())
            if "price_risk_fraction" in frame.columns
            else 0
        ),
        "insufficient_net_rr": (
            int((frame["net_reward_risk"] < 1.20).sum())
            if "net_reward_risk" in frame.columns
            else 0
        ),
    }


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    combined_frames: list[pd.DataFrame] = []

    for label, start, end in week_segments(research):
        market, _ = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=3 * 24 * 60,
        )
        one_minute = to_auction_bars(market)
        index_by_ts = {bar.ts_event_ns: index for index, bar in enumerate(one_minute)}
        detector = DiagnosticDetector(candidate)
        for bar in aggregate_five_minute(market):
            detector.on_bar(bar)
        evidence = pd.DataFrame(asdict(row) for row in detector.evidence)
        rows = [
            path_row(
                pending=pending,
                bars=one_minute,
                index_by_ts=index_by_ts,
                cost=cost,
            )
            for pending_rows in detector.schedules["two-touch-resting-pool"].values()
            for pending in pending_rows
            if start
            <= pd.Timestamp(
                pending.plan.signal_time_ns,
                unit="ns",
                tz="UTC",
            ).to_pydatetime()
            < end
        ]
        paths = pd.DataFrame(rows)
        if not paths.empty and not evidence.empty:
            joined = paths.merge(
                evidence,
                on="scenario_id",
                how="left",
                validate="one_to_one",
            )
        else:
            joined = paths
        joined["segment"] = label
        joined.to_csv(output / f"{label}_candidates.csv", index=False)
        atomic_json(output / f"{label}_rejections.json", detector.rejections)
        if not joined.empty:
            combined_frames.append(joined)

    combined = (
        pd.concat(combined_frames, ignore_index=True)
        if combined_frames
        else pd.DataFrame()
    )
    combined.to_csv(output / "combined_candidates.csv", index=False)
    summaries: dict[str, Any] = {}
    for rule in RULES:
        selected = (
            combined.loc[rule_mask(combined, rule).fillna(False)].copy()
            if not combined.empty
            else pd.DataFrame()
        )
        by_segment = {
            segment: summarize(group)
            for segment, group in selected.groupby("segment", sort=True)
        }
        summaries[rule] = {
            "combined": summarize(selected),
            "segments": by_segment,
        }
    flow_bins = pd.cut(
        combined.get("confirmation_flow_z", pd.Series(dtype=float)),
        bins=[-float("inf"), 0.50, 1.00, 1.70, float("inf")],
        labels=["0.00-0.50", "0.50-1.00", "1.00-1.70", ">=1.70"],
        right=False,
    )
    flow_table: dict[str, Any] = {}
    if not combined.empty:
        for label, group in combined.groupby(flow_bins, observed=True):
            flow_table[str(label)] = summarize(group)
    payload = {
        "scenario": "resting-pool first structure-break confirmation diagnosis",
        "all_in_cost_bps_per_side": float(
            execution["all_in_cost_bps_per_side"],
        ),
        "entry_delay_bars": 1,
        "same_bar_ambiguity": "stop_first",
        "total_candidates": int(len(combined)),
        "flow_bins": flow_table,
        "rules": summaries,
    }
    atomic_json(output / "resting_pool_confirmation_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-resting-pools",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-resting-pool-confirmation",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
