#!/usr/bin/env python3
"""Causal forced-deleveraging exhaustion reversal scenarios.

A fast directional move can be informed price discovery or a reflexive
liquidation cascade.  This detector identifies a five-minute price/flow shock,
then waits for completed-bar evidence that extension has stopped and opposite
aggressive flow has appeared.  Official Binance USD-M open interest, premium
index and mark price distinguish forced deleveraging and venue dislocation from
an ordinary momentum bar.

Four predeclared variants share the same price/flow event and differ only in
required causal state.  Plans are executed by the common candidate-01 portfolio
simulator with one global position, one-bar delay, structural loss sizing and
7 bps-per-side stress cost.  Only five frozen one-week segments run initially.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
from math import log
from pathlib import Path
import statistics
import sys
from typing import Any, Deque

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auxiliary_data import (  # noqa: E402
    download_auxiliary,
    read_index_klines,
    read_metrics,
)
from core import AuctionBar, CandidateConfig, Response, Side, TradePlan  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from portfolio_probe import Pending, Variant, _aggregate_variant, simulate  # noqa: E402


RISK_RATES = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)
RULES = (
    "price-flow-exhaustion",
    "oi-liquidation-exhaustion",
    "oi-premium-exhaustion",
    "oi-mark-dislocation-exhaustion",
)
SHOCK_BARS = 5


@dataclass(frozen=True, slots=True)
class EnrichedBar:
    bar: AuctionBar
    oi_pct_15: float | None
    oi_pct_15_z: float | None
    premium_close: float | None
    premium_z_120: float | None
    mark_close: float | None


@dataclass(frozen=True, slots=True)
class BarState:
    atr: float | None
    flow_z: float | None
    volume_z: float | None
    body_atr: float | None


@dataclass(slots=True)
class Shock:
    reversal_side: Side
    origin: float
    extreme: float
    atr: float
    shock_start_index: int
    signal_index: int
    expiry_index: int
    shock_atr: float
    path_efficiency: float
    cascade_flow_mean_z: float
    maximum_volume_z: float
    oi_liquidation: bool
    premium_dislocation: bool
    mark_dislocation: bool
    oi_pct_15: float | None
    oi_pct_15_z: float | None
    premium_z: float | None
    last_mark_dislocation_bps: float | None


@dataclass(frozen=True, slots=True)
class Evidence:
    rule: str
    scenario_id: str
    signal_time_ns: int
    side: str
    shock_atr: float
    shock_path_efficiency: float
    cascade_flow_mean_z: float
    maximum_volume_z: float
    oi_pct_15: float | None
    oi_pct_15_z: float | None
    cascade_aligned_premium_z: float | None
    cascade_aligned_last_mark_bps: float | None
    reversal_flow_z: float
    reversal_body_atr: float
    retracement_atr: float
    premium_snap: float | None
    stop_distance_atr: float
    target_distance_atr: float


class LiquidationExhaustionDetector:
    def __init__(self, config: CandidateConfig) -> None:
        self.config = config
        self.history: Deque[EnrichedBar] = deque(maxlen=SHOCK_BARS + 1)
        self.true_ranges: Deque[float] = deque(maxlen=config.atr_lookback)
        self.flows: Deque[float] = deque(maxlen=config.flow_lookback)
        self.log_volumes: Deque[float] = deque(maxlen=config.volume_lookback)
        self.previous_close: float | None = None
        self.index = 0
        self.cooldown_until = -1
        self.shock: Shock | None = None
        self.schedules: dict[str, dict[int, list[Pending]]] = {
            rule: {} for rule in RULES
        }
        self.evidence: list[Evidence] = []
        self.rejections: dict[str, int] = {
            "insufficient_history": 0,
            "not_directional_shock": 0,
            "shock_expired": 0,
            "invalid_geometry": 0,
            "cooldown": 0,
        }

    @staticmethod
    def _zscore(value: float, history: Deque[float]) -> float | None:
        if len(history) < 20:
            return None
        mean = statistics.fmean(history)
        variance = statistics.fmean((item - mean) ** 2 for item in history)
        return 0.0 if variance <= 0.0 else (value - mean) / variance**0.5

    def _bar_state(self, item: EnrichedBar) -> BarState:
        atr = statistics.fmean(self.true_ranges) if len(self.true_ranges) >= 20 else None
        flow_z = self._zscore(item.bar.aggressive_imbalance, self.flows)
        volume_z = self._zscore(log(max(item.bar.quote_volume, 1e-12)), self.log_volumes)
        body_atr = (
            (item.bar.close - item.bar.open) / atr
            if atr is not None and atr > 0.0
            else None
        )
        return BarState(atr=atr, flow_z=flow_z, volume_z=volume_z, body_atr=body_atr)

    @staticmethod
    def _optional(value: float | None, fallback: float = 0.0) -> float:
        return fallback if value is None or not np.isfinite(value) else float(value)

    def _detect_shock(
        self,
        *,
        item: EnrichedBar,
        state: BarState,
    ) -> Shock | None:
        if (
            len(self.history) < SHOCK_BARS
            or state.atr is None
            or state.flow_z is None
            or state.volume_z is None
        ):
            self.rejections["insufficient_history"] += 1
            return None
        window = list(self.history)[-(SHOCK_BARS - 1) :] + [item]
        closes = [row.bar.close for row in window]
        origin = window[0].bar.open
        change = closes[-1] - origin
        if change == 0.0:
            self.rejections["not_directional_shock"] += 1
            return None
        cascade_sign = 1.0 if change > 0.0 else -1.0
        shock_atr = abs(change) / state.atr
        path_length = sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes)))
        path_efficiency = abs(closes[-1] - closes[0]) / path_length if path_length > 0.0 else 0.0

        historical_flows = list(self.flows)
        flow_z_values: list[float] = []
        volume_z_values: list[float] = []
        for row in window:
            flow = row.bar.aggressive_imbalance
            flow_z = self._zscore(flow, deque(historical_flows, maxlen=self.config.flow_lookback))
            if flow_z is not None:
                flow_z_values.append(cascade_sign * flow_z)
            volume_z_values.append(
                self._optional(
                    self._zscore(
                        log(max(row.bar.quote_volume, 1e-12)),
                        self.log_volumes,
                    ),
                ),
            )
        if not flow_z_values:
            self.rejections["insufficient_history"] += 1
            return None
        cascade_flow = statistics.fmean(flow_z_values)
        maximum_volume_z = max(volume_z_values)
        directional = (
            shock_atr >= 1.50
            and path_efficiency >= 0.72
            and cascade_flow >= 0.45
            and maximum_volume_z >= 1.25
        )
        if not directional:
            self.rejections["not_directional_shock"] += 1
            return None

        reversal_side = Side.SHORT if cascade_sign > 0.0 else Side.LONG
        extreme = max(row.bar.high for row in window) if cascade_sign > 0.0 else min(row.bar.low for row in window)
        oi_pct_15 = item.oi_pct_15
        oi_pct_15_z = item.oi_pct_15_z
        oi_liquidation = (
            (oi_pct_15 is not None and oi_pct_15 <= -0.0025)
            or (oi_pct_15_z is not None and oi_pct_15_z <= -1.0)
        )
        premium_z = item.premium_z_120
        cascade_aligned_premium = (
            cascade_sign * premium_z if premium_z is not None else None
        )
        premium_dislocation = (
            cascade_aligned_premium is not None
            and cascade_aligned_premium >= 1.0
        )
        mark_dislocation_bps = None
        if item.mark_close is not None and item.mark_close > 0.0:
            mark_dislocation_bps = cascade_sign * (
                item.bar.close / item.mark_close - 1.0
            ) * 10_000.0
        mark_dislocation = (
            mark_dislocation_bps is not None and mark_dislocation_bps >= 3.0
        )
        return Shock(
            reversal_side=reversal_side,
            origin=origin,
            extreme=extreme,
            atr=state.atr,
            shock_start_index=self.index - SHOCK_BARS + 1,
            signal_index=self.index,
            expiry_index=self.index + 8,
            shock_atr=shock_atr,
            path_efficiency=path_efficiency,
            cascade_flow_mean_z=cascade_flow,
            maximum_volume_z=maximum_volume_z,
            oi_liquidation=oi_liquidation,
            premium_dislocation=premium_dislocation,
            mark_dislocation=mark_dislocation,
            oi_pct_15=oi_pct_15,
            oi_pct_15_z=oi_pct_15_z,
            premium_z=cascade_aligned_premium,
            last_mark_dislocation_bps=mark_dislocation_bps,
        )

    @staticmethod
    def _minimum_stop(entry: float, stop: float, side: Side, atr: float) -> float:
        minimum = 0.75 * atr
        return min(stop, entry - minimum) if side is Side.LONG else max(stop, entry + minimum)

    def _emit(
        self,
        *,
        rule: str,
        item: EnrichedBar,
        state: BarState,
        shock: Shock,
        retracement_atr: float,
        premium_snap: float | None,
    ) -> Pending | None:
        side = shock.reversal_side
        entry = item.bar.close
        raw_stop = (
            shock.extreme - 0.15 * shock.atr
            if side is Side.LONG
            else shock.extreme + 0.15 * shock.atr
        )
        stop = self._minimum_stop(entry, raw_stop, side, shock.atr)
        target = shock.origin
        geometry = stop < entry < target if side is Side.LONG else target < entry < stop
        if not geometry:
            self.rejections["invalid_geometry"] += 1
            return None
        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = reward / risk if risk > 0.0 else 0.0
        if rr < 1.35:
            self.rejections["invalid_geometry"] += 1
            return None
        scenario_id = (
            f"liquidation:{rule}:{shock.signal_index}:{side.value.lower()}:{item.bar.ts_event_ns}"
        )
        plan = TradePlan(
            scenario_id=scenario_id,
            side=side,
            response=Response.SWEEP_FAILURE,
            signal_time_ns=item.bar.ts_event_ns,
            observed_time_ns=item.bar.ts_event_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            anchor_high=max(shock.origin, shock.extreme),
            anchor_low=min(shock.origin, shock.extreme),
            sweep_extreme=shock.extreme,
            atr=shock.atr,
            estimated_reward_risk=rr,
            max_hold_bars=120,
            reason_code="FORCED_DELEVERAGING_EXHAUSTION_CONFIRMED",
        )
        pending = Pending(symbol="BTCUSDT", horizon=SHOCK_BARS, plan=plan)
        self.schedules[rule].setdefault(item.bar.ts_event_ns, []).append(pending)
        self.evidence.append(
            Evidence(
                rule=rule,
                scenario_id=scenario_id,
                signal_time_ns=item.bar.ts_event_ns,
                side=side.value,
                shock_atr=shock.shock_atr,
                shock_path_efficiency=shock.path_efficiency,
                cascade_flow_mean_z=shock.cascade_flow_mean_z,
                maximum_volume_z=shock.maximum_volume_z,
                oi_pct_15=shock.oi_pct_15,
                oi_pct_15_z=shock.oi_pct_15_z,
                cascade_aligned_premium_z=shock.premium_z,
                cascade_aligned_last_mark_bps=shock.last_mark_dislocation_bps,
                reversal_flow_z=side.sign * float(state.flow_z or 0.0),
                reversal_body_atr=side.sign * float(state.body_atr or 0.0),
                retracement_atr=retracement_atr,
                premium_snap=premium_snap,
                stop_distance_atr=abs(entry - stop) / shock.atr,
                target_distance_atr=abs(target - entry) / shock.atr,
            ),
        )
        return pending

    def _confirm(
        self,
        *,
        item: EnrichedBar,
        state: BarState,
    ) -> None:
        shock = self.shock
        if shock is None or state.flow_z is None or state.body_atr is None:
            return
        if self.index > shock.expiry_index:
            self.rejections["shock_expired"] += 1
            self.shock = None
            return
        if self.index <= shock.signal_index:
            return
        side = shock.reversal_side
        if side is Side.LONG:
            shock.extreme = min(shock.extreme, item.bar.low)
            retracement_atr = (item.bar.close - shock.extreme) / shock.atr
        else:
            shock.extreme = max(shock.extreme, item.bar.high)
            retracement_atr = (shock.extreme - item.bar.close) / shock.atr
        reversal_flow = side.sign * state.flow_z
        reversal_body = side.sign * state.body_atr
        premium_snap = None
        if shock.premium_z is not None and item.premium_z_120 is not None:
            cascade_sign = -float(side.sign)
            current_aligned = cascade_sign * item.premium_z_120
            premium_snap = shock.premium_z - current_aligned
        confirmed = (
            retracement_atr >= 0.40
            and reversal_flow >= 0.50
            and reversal_body >= 0.20
        )
        if not confirmed:
            return
        base = self._emit(
            rule="price-flow-exhaustion",
            item=item,
            state=state,
            shock=shock,
            retracement_atr=retracement_atr,
            premium_snap=premium_snap,
        )
        if shock.oi_liquidation:
            self._emit(
                rule="oi-liquidation-exhaustion",
                item=item,
                state=state,
                shock=shock,
                retracement_atr=retracement_atr,
                premium_snap=premium_snap,
            )
        if shock.oi_liquidation and shock.premium_dislocation:
            self._emit(
                rule="oi-premium-exhaustion",
                item=item,
                state=state,
                shock=shock,
                retracement_atr=retracement_atr,
                premium_snap=premium_snap,
            )
        if shock.oi_liquidation and shock.mark_dislocation:
            self._emit(
                rule="oi-mark-dislocation-exhaustion",
                item=item,
                state=state,
                shock=shock,
                retracement_atr=retracement_atr,
                premium_snap=premium_snap,
            )
        if base is not None:
            self.cooldown_until = self.index + 30
        self.shock = None

    def on_bar(self, item: EnrichedBar) -> None:
        self.index += 1
        state = self._bar_state(item)
        self._confirm(item=item, state=state)
        if self.index <= self.cooldown_until:
            self.rejections["cooldown"] += 1
        elif self.shock is None:
            detected = self._detect_shock(item=item, state=state)
            if detected is not None:
                self.shock = detected

        previous = self.previous_close
        true_range = (
            item.bar.high - item.bar.low
            if previous is None
            else max(
                item.bar.high - item.bar.low,
                abs(item.bar.high - previous),
                abs(item.bar.low - previous),
            )
        )
        self.true_ranges.append(true_range)
        self.flows.append(item.bar.aggressive_imbalance)
        self.log_volumes.append(log(max(item.bar.quote_volume, 1e-12)))
        self.previous_close = item.bar.close
        self.history.append(item)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def week_segments(research: dict[str, Any]) -> list[tuple[str, datetime, datetime]]:
    def week(label: str, value: str) -> tuple[str, datetime, datetime]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7)

    return [
        week("discovery", str(research["discovery_week"])),
        *[
            week(f"confirmation-{index + 1}", value)
            for index, value in enumerate(research["confirmation_weeks"])
        ],
        *[
            week(f"untouched-{index + 1}", value)
            for index, value in enumerate(research.get("additional_random_weeks", []))
        ],
    ]


def _merge_asof(left: pd.DataFrame, right: pd.DataFrame, right_time: str) -> pd.DataFrame:
    if right.empty:
        return left
    return pd.merge_asof(
        left.sort_values("event_time", kind="stable"),
        right.sort_values(right_time, kind="stable"),
        left_on="event_time",
        right_on=right_time,
        direction="backward",
        allow_exact_matches=True,
    )


def enrich(
    frame: pd.DataFrame,
    metrics: pd.DataFrame,
    premium: pd.DataFrame,
    mark: pd.DataFrame,
) -> list[EnrichedBar]:
    bars = to_auction_bars(frame)
    joined = pd.DataFrame(
        {
            "row": range(len(frame)),
            "event_time": pd.to_datetime(frame["close_dt"], utc=True).astype("datetime64[ns, UTC]"),
        },
    )
    joined = _merge_asof(joined, metrics, "create_time")
    joined = _merge_asof(joined, premium, "close_time")
    if not mark.empty:
        renamed = mark.rename(columns={"close_time": "mark_close_time"})
        joined = _merge_asof(joined, renamed, "mark_close_time")
    joined = joined.sort_values("row", kind="stable").reset_index(drop=True)

    def value(row: Any, name: str) -> float | None:
        raw = getattr(row, name, None)
        return float(raw) if raw is not None and pd.notna(raw) else None

    return [
        EnrichedBar(
            bar=bar,
            oi_pct_15=value(row, "oi_pct_15"),
            oi_pct_15_z=value(row, "oi_pct_15_z"),
            premium_close=value(row, "premium_close"),
            premium_z_120=value(row, "premium_z_120"),
            mark_close=value(row, "mark_close"),
        )
        for bar, row in zip(bars, joined.itertuples(index=False), strict=True)
    ]


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    risk_rates = tuple(float(value) for value in args.risk_rates.split(","))
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    aggregate_rows: dict[str, list[dict[str, Any]]] = {rule: [] for rule in RULES}
    manifests: list[dict[str, Any]] = []
    plan_counts: dict[str, dict[str, int]] = {}

    for label, start, end in week_segments(research):
        fetch_start = start - timedelta(days=2)
        frame, market_records = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache / "market",
            warmup_minutes=2 * 24 * 60,
        )
        auxiliary_records = download_auxiliary(
            data_types=("metrics", "premiumIndexKlines", "markPriceKlines"),
            symbol="BTCUSDT",
            start=fetch_start,
            end=end,
            cache_dir=args.cache / "auxiliary",
            workers=args.workers,
        )
        manifests.extend({"source": "klines", **asdict(record)} for record in market_records)
        manifests.extend({"source": record.data_type, **record.to_dict()} for record in auxiliary_records)
        metrics = read_metrics(auxiliary_records)
        premium = read_index_klines(
            auxiliary_records,
            data_type="premiumIndexKlines",
            prefix="premium",
        )
        mark = read_index_klines(
            auxiliary_records,
            data_type="markPriceKlines",
            prefix="mark",
        )
        enriched = enrich(frame, metrics, premium, mark)
        bars = [item.bar for item in enriched]
        detector = LiquidationExhaustionDetector(candidate)
        for item in enriched:
            detector.on_bar(item)
        pd.DataFrame(asdict(row) for row in detector.evidence).to_csv(
            output / f"{label}_evidence.csv",
            index=False,
        )
        atomic_json(output / f"{label}_rejections.json", detector.rejections)
        plan_counts[label] = {
            rule: sum(len(rows) for rows in detector.schedules[rule].values())
            for rule in RULES
        }

        for rule in RULES:
            schedule = {
                timestamp: tuple(rows)
                for timestamp, rows in detector.schedules[rule].items()
            }
            trades, metrics_row, daily = simulate(
                variant=Variant(rule, ("BTCUSDT",), (SHOCK_BARS,)),
                bars_by_symbol={"BTCUSDT": bars},
                evaluation_start=start,
                evaluation_end=end,
                base_candidate=candidate,
                cost=cost,
                minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
                minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
                starting_nav=float(execution["starting_nav"]),
                risk_rates=risk_rates,
                allowed_scenario_ids=frozenset(),
                external_plans_by_signal_time=schedule,
            )
            destination = output / rule / label
            destination.mkdir(parents=True, exist_ok=True)
            trades.to_csv(destination / "trades.csv", index=False)
            atomic_json(destination / "metrics.json", metrics_row)
            for risk, rows in daily.items():
                pd.DataFrame(rows).to_csv(
                    destination / f"daily_nav_{risk:.4f}.csv",
                    index=False,
                )
            aggregate_rows[rule].append(metrics_row)

    aggregates: dict[str, Any] = {}
    for rule, rows in aggregate_rows.items():
        aggregate = _aggregate_variant(rows, risk_rates)
        aggregate["all_segments_positive_at_one_percent_risk"] = all(
            float(row["risk_metrics"]["0.0100"]["total_return"]) > 0.0
            for row in rows
        )
        aggregate["minimum_segment_trades"] = min(int(row["trades"]) for row in rows)
        aggregates[rule] = aggregate
        atomic_json(output / rule / "aggregate_metrics.json", aggregate)

    files = pd.DataFrame(manifests)
    if not files.empty:
        files = files.drop_duplicates(["source", "path"])
    atomic_json(
        output / "download_manifest.json",
        {"provider": "Binance Vision", "records": files.to_dict(orient="records")},
    )
    summary = {
        "scenario": "forced deleveraging exhaustion reversal",
        "shock_window_minutes": SHOCK_BARS,
        "long_evaluation_run": False,
        "one_global_position": True,
        "one_bar_execution_delay": True,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "plan_counts": plan_counts,
        "aggregates": aggregates,
    }
    atomic_json(output / "liquidation_exhaustion_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-liquidation-exhaustion",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-liquidation-exhaustion",
    )
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument(
        "--risk-rates",
        default=",".join(str(value) for value in RISK_RATES),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
