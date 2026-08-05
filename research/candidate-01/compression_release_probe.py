#!/usr/bin/env python3
"""Causal balance-compression and inventory-release continuation scenarios.

A durable continuation should not be inferred from an isolated large candle.
This detector requires a completed rotational balance, then a directional
order-flow shock which relocates value.  The inventory variants additionally
require official Binance USD-M open interest to remain accumulated rather than
collapse, separating new-position price discovery from a liquidation-only
print.  Direct and retest entries are evaluated independently.

The detector emits plans only.  The shared candidate-01 portfolio simulator
keeps one global position and applies the existing one-minute delay, structural
risk sizing, 7 bps-per-side stress cost, NAV accounting and margin diagnostics.
Only five frozen one-week segments run initially.
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

from auxiliary_data import download_auxiliary, read_metrics  # noqa: E402
from core import AuctionBar, CandidateConfig, Response, Side, TradePlan  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from portfolio_probe import Pending, Variant, _aggregate_variant, simulate  # noqa: E402


RISK_RATES = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)
RULES = (
    "price-compression-direct",
    "price-compression-retest",
    "inventory-compression-direct",
    "inventory-compression-retest",
)
BALANCE_BARS = 90


@dataclass(frozen=True, slots=True)
class EnrichedBar:
    bar: AuctionBar
    oi: float | None
    oi_pct_15: float | None
    oi_pct_60: float | None
    oi_pct_15_z: float | None


@dataclass(frozen=True, slots=True)
class Balance:
    high: float
    low: float
    midpoint: float
    width: float
    width_atr: float
    path_efficiency: float
    midpoint_crossings: int
    central_fraction: float
    flow_mean: float
    start_time_ns: int
    end_time_ns: int


@dataclass(slots=True)
class RetestState:
    side: Side
    boundary: float
    projection_target: float
    balance: Balance
    atr: float
    inventory_confirmed: bool
    signal_index: int
    expiry_index: int
    breakout_extreme: float


@dataclass(frozen=True, slots=True)
class Evidence:
    rule: str
    scenario_id: str
    signal_time_ns: int
    side: str
    balance_high: float
    balance_low: float
    width_atr: float
    path_efficiency: float
    midpoint_crossings: int
    central_fraction: float
    flow_mean: float
    breakout_flow_z: float
    breakout_volume_z: float
    breakout_body_atr: float
    oi_pct_15: float | None
    oi_pct_60: float | None
    oi_pct_15_z: float | None
    entry_style: str


class CompressionReleaseDetector:
    def __init__(self, config: CandidateConfig) -> None:
        self.config = config
        self.history: Deque[EnrichedBar] = deque(maxlen=BALANCE_BARS)
        self.true_ranges: Deque[float] = deque(maxlen=config.atr_lookback)
        self.flows: Deque[float] = deque(maxlen=config.flow_lookback)
        self.log_volumes: Deque[float] = deque(maxlen=config.volume_lookback)
        self.previous_close: float | None = None
        self.index = 0
        self.cooldown_until = -1
        self.retest: RetestState | None = None
        self.schedules: dict[str, dict[int, list[Pending]]] = {
            rule: {} for rule in RULES
        }
        self.evidence: list[Evidence] = []
        self.rejections: dict[str, int] = {
            "no_balance": 0,
            "not_breakout": 0,
            "invalid_geometry": 0,
            "expired_retest": 0,
            "cooldown": 0,
        }

    @staticmethod
    def _zscore(value: float, history: Deque[float]) -> float | None:
        if len(history) < 20:
            return None
        mean = statistics.fmean(history)
        variance = statistics.fmean((item - mean) ** 2 for item in history)
        return 0.0 if variance <= 0.0 else (value - mean) / variance**0.5

    def _bar_state(self, item: EnrichedBar) -> tuple[float | None, float | None, float | None, float | None]:
        atr = statistics.fmean(self.true_ranges) if len(self.true_ranges) >= 20 else None
        flow_z = self._zscore(item.bar.aggressive_imbalance, self.flows)
        volume_z = self._zscore(log(max(item.bar.quote_volume, 1e-12)), self.log_volumes)
        body_atr = (
            (item.bar.close - item.bar.open) / atr
            if atr is not None and atr > 0.0
            else None
        )
        return atr, flow_z, volume_z, body_atr

    def _balance(self, atr: float) -> Balance | None:
        if len(self.history) < BALANCE_BARS or atr <= 0.0:
            return None
        rows = list(self.history)
        highs = [item.bar.high for item in rows]
        lows = [item.bar.low for item in rows]
        closes = [item.bar.close for item in rows]
        high = max(highs)
        low = min(lows)
        width = high - low
        if width <= 0.0:
            return None
        width_atr = width / atr
        increments = [abs(closes[index] - closes[index - 1]) for index in range(1, len(closes))]
        path_length = sum(increments)
        path_efficiency = (
            abs(closes[-1] - rows[0].bar.open) / path_length
            if path_length > 0.0
            else 0.0
        )
        midpoint = 0.5 * (high + low)
        signs = [1 if close > midpoint else -1 if close < midpoint else 0 for close in closes]
        crossings = sum(
            1
            for index in range(1, len(signs))
            if signs[index] != 0 and signs[index - 1] != 0 and signs[index] != signs[index - 1]
        )
        lower = low + 0.25 * width
        upper = high - 0.25 * width
        central = statistics.fmean(1.0 if lower <= close <= upper else 0.0 for close in closes)
        flow_mean = statistics.fmean(item.bar.aggressive_imbalance for item in rows)
        valid = (
            2.25 <= width_atr <= 6.50
            and path_efficiency <= 0.35
            and crossings >= 4
            and central >= 0.45
            and abs(flow_mean) <= 0.12
        )
        if not valid:
            return None
        return Balance(
            high=high,
            low=low,
            midpoint=midpoint,
            width=width,
            width_atr=width_atr,
            path_efficiency=path_efficiency,
            midpoint_crossings=crossings,
            central_fraction=central,
            flow_mean=flow_mean,
            start_time_ns=rows[0].bar.ts_event_ns,
            end_time_ns=rows[-1].bar.ts_event_ns,
        )

    @staticmethod
    def _inventory_confirmed(item: EnrichedBar) -> bool:
        return (
            item.oi_pct_60 is not None
            and item.oi_pct_15 is not None
            and item.oi_pct_60 >= 0.002
            and item.oi_pct_15 >= 0.0
        )

    @staticmethod
    def _minimum_stop(entry: float, stop: float, side: Side, atr: float) -> float:
        minimum = 0.75 * atr
        return min(stop, entry - minimum) if side is Side.LONG else max(stop, entry + minimum)

    def _plan(
        self,
        *,
        rule: str,
        item: EnrichedBar,
        side: Side,
        balance: Balance,
        atr: float,
        stop: float,
        target: float,
        flow_z: float,
        volume_z: float,
        body_atr: float,
        style: str,
        reason: str,
        extreme: float,
    ) -> Pending | None:
        entry = item.bar.close
        stop = self._minimum_stop(entry, stop, side, atr)
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
            f"compression:{rule}:{balance.end_time_ns}:{side.value.lower()}:{item.bar.ts_event_ns}"
        )
        plan = TradePlan(
            scenario_id=scenario_id,
            side=side,
            response=Response.ACCEPTANCE_FAILURE,
            signal_time_ns=item.bar.ts_event_ns,
            observed_time_ns=item.bar.ts_event_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            anchor_high=balance.high,
            anchor_low=balance.low,
            sweep_extreme=extreme,
            atr=atr,
            estimated_reward_risk=rr,
            max_hold_bars=180,
            reason_code=reason,
        )
        pending = Pending(symbol="BTCUSDT", horizon=BALANCE_BARS, plan=plan)
        self.schedules[rule].setdefault(item.bar.ts_event_ns, []).append(pending)
        self.evidence.append(
            Evidence(
                rule=rule,
                scenario_id=scenario_id,
                signal_time_ns=item.bar.ts_event_ns,
                side=side.value,
                balance_high=balance.high,
                balance_low=balance.low,
                width_atr=balance.width_atr,
                path_efficiency=balance.path_efficiency,
                midpoint_crossings=balance.midpoint_crossings,
                central_fraction=balance.central_fraction,
                flow_mean=balance.flow_mean,
                breakout_flow_z=flow_z,
                breakout_volume_z=volume_z,
                breakout_body_atr=body_atr,
                oi_pct_15=item.oi_pct_15,
                oi_pct_60=item.oi_pct_60,
                oi_pct_15_z=item.oi_pct_15_z,
                entry_style=style,
            ),
        )
        return pending

    def _direct_plans(
        self,
        *,
        item: EnrichedBar,
        side: Side,
        balance: Balance,
        atr: float,
        flow_z: float,
        volume_z: float,
        body_atr: float,
        inventory: bool,
    ) -> None:
        boundary = balance.high if side is Side.LONG else balance.low
        stop = boundary - side.sign * 0.35 * atr
        target = boundary + side.sign * balance.width
        self._plan(
            rule="price-compression-direct",
            item=item,
            side=side,
            balance=balance,
            atr=atr,
            stop=stop,
            target=target,
            flow_z=flow_z,
            volume_z=volume_z,
            body_atr=body_atr,
            style="next-bar-direct",
            reason="BALANCE_VALUE_RELOCATION_DIRECT",
            extreme=(item.bar.low if side is Side.LONG else item.bar.high),
        )
        if inventory:
            self._plan(
                rule="inventory-compression-direct",
                item=item,
                side=side,
                balance=balance,
                atr=atr,
                stop=stop,
                target=target,
                flow_z=flow_z,
                volume_z=volume_z,
                body_atr=body_atr,
                style="next-bar-direct",
                reason="INVENTORY_BUILD_VALUE_RELOCATION_DIRECT",
                extreme=(item.bar.low if side is Side.LONG else item.bar.high),
            )

    def _open_retest(
        self,
        *,
        item: EnrichedBar,
        side: Side,
        balance: Balance,
        atr: float,
        inventory: bool,
    ) -> None:
        boundary = balance.high if side is Side.LONG else balance.low
        self.retest = RetestState(
            side=side,
            boundary=boundary,
            projection_target=boundary + side.sign * balance.width,
            balance=balance,
            atr=atr,
            inventory_confirmed=inventory,
            signal_index=self.index,
            expiry_index=self.index + 15,
            breakout_extreme=(item.bar.high if side is Side.LONG else item.bar.low),
        )

    def _observe_retest(
        self,
        *,
        item: EnrichedBar,
        flow_z: float,
        volume_z: float,
        body_atr: float,
    ) -> None:
        state = self.retest
        if state is None:
            return
        if self.index > state.expiry_index:
            self.rejections["expired_retest"] += 1
            self.retest = None
            return
        if self.index <= state.signal_index:
            return
        bar = item.bar
        if state.side is Side.LONG:
            accepted = (
                bar.low <= state.boundary + 0.20 * state.atr
                and bar.close >= state.boundary + 0.05 * state.atr
                and body_atr >= 0.08
                and flow_z >= 0.0
            )
            stop = min(bar.low, state.boundary) - 0.15 * state.atr
        else:
            accepted = (
                bar.high >= state.boundary - 0.20 * state.atr
                and bar.close <= state.boundary - 0.05 * state.atr
                and body_atr <= -0.08
                and flow_z <= 0.0
            )
            stop = max(bar.high, state.boundary) + 0.15 * state.atr
        if not accepted:
            return
        self._plan(
            rule="price-compression-retest",
            item=item,
            side=state.side,
            balance=state.balance,
            atr=state.atr,
            stop=stop,
            target=state.projection_target,
            flow_z=flow_z,
            volume_z=volume_z,
            body_atr=body_atr,
            style="outside-edge-retest",
            reason="BALANCE_VALUE_RELOCATION_RETEST",
            extreme=state.breakout_extreme,
        )
        if state.inventory_confirmed:
            self._plan(
                rule="inventory-compression-retest",
                item=item,
                side=state.side,
                balance=state.balance,
                atr=state.atr,
                stop=stop,
                target=state.projection_target,
                flow_z=flow_z,
                volume_z=volume_z,
                body_atr=body_atr,
                style="outside-edge-retest",
                reason="INVENTORY_BUILD_VALUE_RELOCATION_RETEST",
                extreme=state.breakout_extreme,
            )
        self.retest = None
        self.cooldown_until = self.index + BALANCE_BARS

    def on_bar(self, item: EnrichedBar) -> None:
        self.index += 1
        atr, flow_z, volume_z, body_atr = self._bar_state(item)
        if atr is not None and flow_z is not None and volume_z is not None and body_atr is not None:
            self._observe_retest(
                item=item,
                flow_z=flow_z,
                volume_z=volume_z,
                body_atr=body_atr,
            )
            if self.index <= self.cooldown_until:
                self.rejections["cooldown"] += 1
            elif self.retest is None:
                balance = self._balance(atr)
                if balance is None:
                    self.rejections["no_balance"] += 1
                else:
                    long_break = (
                        item.bar.close >= balance.high + 0.15 * atr
                        and body_atr >= 0.60
                        and flow_z >= 1.00
                        and volume_z >= 0.50
                    )
                    short_break = (
                        item.bar.close <= balance.low - 0.15 * atr
                        and body_atr <= -0.60
                        and flow_z <= -1.00
                        and volume_z >= 0.50
                    )
                    if long_break or short_break:
                        side = Side.LONG if long_break else Side.SHORT
                        inventory = self._inventory_confirmed(item)
                        self._direct_plans(
                            item=item,
                            side=side,
                            balance=balance,
                            atr=atr,
                            flow_z=flow_z,
                            volume_z=volume_z,
                            body_atr=body_atr,
                            inventory=inventory,
                        )
                        self._open_retest(
                            item=item,
                            side=side,
                            balance=balance,
                            atr=atr,
                            inventory=inventory,
                        )
                    else:
                        self.rejections["not_breakout"] += 1

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


def enrich(frame: pd.DataFrame, metrics: pd.DataFrame) -> list[EnrichedBar]:
    bars = to_auction_bars(frame)
    left = pd.DataFrame(
        {
            "row": range(len(frame)),
            "event_time": pd.to_datetime(frame["close_dt"], utc=True).astype("datetime64[ns, UTC]"),
        },
    ).sort_values("event_time", kind="stable")
    if metrics.empty:
        joined = left.copy()
    else:
        joined = pd.merge_asof(
            left,
            metrics.sort_values("create_time", kind="stable"),
            left_on="event_time",
            right_on="create_time",
            direction="backward",
            allow_exact_matches=True,
        )
    joined = joined.sort_values("row", kind="stable").reset_index(drop=True)

    def value(row: Any, name: str) -> float | None:
        raw = getattr(row, name, None)
        return float(raw) if raw is not None and pd.notna(raw) else None

    return [
        EnrichedBar(
            bar=bar,
            oi=value(row, "sum_open_interest"),
            oi_pct_15=value(row, "oi_pct_15"),
            oi_pct_60=value(row, "oi_pct_60"),
            oi_pct_15_z=value(row, "oi_pct_15_z"),
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
            data_types=("metrics",),
            symbol="BTCUSDT",
            start=fetch_start,
            end=end,
            cache_dir=args.cache / "auxiliary",
            workers=args.workers,
        )
        manifests.extend({"source": "klines", **asdict(record)} for record in market_records)
        manifests.extend({"source": "metrics", **record.to_dict()} for record in auxiliary_records)
        metrics = read_metrics(auxiliary_records)
        enriched = enrich(frame, metrics)
        bars = [item.bar for item in enriched]
        detector = CompressionReleaseDetector(candidate)
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
                variant=Variant(rule, ("BTCUSDT",), (BALANCE_BARS,)),
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
        "scenario": "rotational balance compression and inventory release",
        "balance_minutes": BALANCE_BARS,
        "long_evaluation_run": False,
        "one_global_position": True,
        "one_bar_execution_delay": True,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "plan_counts": plan_counts,
        "aggregates": aggregates,
    }
    atomic_json(output / "compression_release_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-compression-release",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-compression-release",
    )
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument(
        "--risk-rates",
        default=",".join(str(value) for value in RISK_RATES),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
