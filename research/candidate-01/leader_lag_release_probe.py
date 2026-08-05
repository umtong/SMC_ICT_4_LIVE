#!/usr/bin/env python3
"""Causal BTC leader-to-alt lag-release continuation scenarios.

BTC often concentrates price discovery before correlated assets fully adjust.
This detector identifies a completed five-minute BTC impulse, measures each
altcoin's ATR-normalized response over the same bars, then waits for the laggard
to produce its own structure break and aligned aggressive flow while BTC still
holds most of the initiating displacement.

Targets are the remaining normalized catch-up distance, not fixed take-profit
multiples.  Stops sit beyond the laggard confirmation sequence.  The shared
candidate-01 portfolio simulator handles one global position, next-bar entry,
costs, risk sizing, NAV and margin.  Five frozen weeks run first.
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

from core import AuctionBar, CandidateConfig, Response, Side, TradePlan  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from portfolio_probe import Pending, Variant, _aggregate_variant, simulate  # noqa: E402


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TARGETS = ("ETHUSDT", "SOLUSDT", "XRPUSDT")
IMPULSE_BARS = 5
RISK_RATES = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)
RULES = (
    "btc-leader-all-lag-release",
    "btc-leader-persistent-release",
    "btc-leader-deepest-persistent",
    "btc-leader-high-impulse",
)


@dataclass(frozen=True, slots=True)
class Snapshot:
    bar: AuctionBar
    atr: float | None
    flow_z: float | None
    volume_z: float | None
    body_atr: float | None
    prior_structure_high: float | None
    prior_structure_low: float | None


class Tracker:
    def __init__(self, config: CandidateConfig) -> None:
        self.config = config
        self.true_ranges: Deque[float] = deque(maxlen=config.atr_lookback)
        self.flows: Deque[float] = deque(maxlen=config.flow_lookback)
        self.log_volumes: Deque[float] = deque(maxlen=config.volume_lookback)
        self.bars: Deque[AuctionBar] = deque(maxlen=30)
        self.previous_close: float | None = None

    @staticmethod
    def zscore(value: float, history: Deque[float]) -> float | None:
        if len(history) < 20:
            return None
        mean = statistics.fmean(history)
        variance = statistics.fmean((item - mean) ** 2 for item in history)
        return 0.0 if variance <= 0.0 else (value - mean) / variance**0.5

    def snapshot(self, bar: AuctionBar) -> Snapshot:
        atr = statistics.fmean(self.true_ranges) if len(self.true_ranges) >= 20 else None
        flow_z = self.zscore(bar.aggressive_imbalance, self.flows)
        volume_z = self.zscore(log(max(bar.quote_volume, 1e-12)), self.log_volumes)
        body_atr = (bar.close - bar.open) / atr if atr is not None and atr > 0.0 else None
        recent = list(self.bars)[-5:]
        return Snapshot(
            bar=bar,
            atr=atr,
            flow_z=flow_z,
            volume_z=volume_z,
            body_atr=body_atr,
            prior_structure_high=max((item.high for item in recent), default=None),
            prior_structure_low=min((item.low for item in recent), default=None),
        )

    def window_with(self, bar: AuctionBar, length: int) -> list[AuctionBar]:
        return list(self.bars)[-(length - 1) :] + [bar]

    def update(self, bar: AuctionBar) -> None:
        previous = self.previous_close
        true_range = (
            bar.high - bar.low
            if previous is None
            else max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))
        )
        self.true_ranges.append(true_range)
        self.flows.append(bar.aggressive_imbalance)
        self.log_volumes.append(log(max(bar.quote_volume, 1e-12)))
        self.bars.append(bar)
        self.previous_close = bar.close


@dataclass(slots=True)
class LagState:
    event_id: str
    symbol: str
    side: Side
    signal_index: int
    expiry_index: int
    btc_origin: float
    btc_end: float
    btc_impulse_atr: float
    btc_impulse_price: float
    alt_origin: float
    alt_atr: float
    expected_alt_move_atr: float
    initial_alt_move_atr: float
    initial_remaining_atr: float
    deepest: bool
    high_impulse: bool
    confirmation_extreme: float


@dataclass(frozen=True, slots=True)
class Evidence:
    rule: str
    scenario_id: str
    event_id: str
    signal_time_ns: int
    symbol: str
    side: str
    btc_impulse_atr: float
    btc_path_efficiency: float
    btc_flow_z: float
    btc_volume_z: float
    initial_alt_move_atr: float
    initial_remaining_atr: float
    confirmation_remaining_atr: float
    btc_hold_fraction: float
    btc_confirmation_flow_z: float
    alt_confirmation_flow_z: float
    alt_confirmation_body_atr: float
    deepest_at_signal: bool
    stop_distance_atr: float
    target_distance_atr: float


class LeaderLagDetector:
    def __init__(self, config: CandidateConfig) -> None:
        self.config = config
        self.trackers = {symbol: Tracker(config) for symbol in SYMBOLS}
        self.index = 0
        self.event_counter = 0
        self.cooldown_until = -1
        self.pending: dict[str, LagState] = {}
        self.schedules: dict[str, dict[int, list[Pending]]] = {
            rule: {} for rule in RULES
        }
        self.evidence: list[Evidence] = []
        self.rejections: dict[str, int] = {
            "insufficient_history": 0,
            "not_btc_impulse": 0,
            "no_laggard": 0,
            "expired_laggard": 0,
            "leader_retraced": 0,
            "invalid_geometry": 0,
            "cooldown": 0,
        }

    @staticmethod
    def _path_efficiency(window: list[AuctionBar]) -> float:
        closes = [bar.close for bar in window]
        path = sum(abs(closes[index] - closes[index - 1]) for index in range(1, len(closes)))
        return abs(closes[-1] - closes[0]) / path if path > 0.0 else 0.0

    def _detect_btc_impulse(
        self,
        *,
        snapshots: dict[str, Snapshot],
        bars_now: dict[str, AuctionBar],
    ) -> tuple[Side, float, float, float] | None:
        btc = snapshots["BTCUSDT"]
        tracker = self.trackers["BTCUSDT"]
        window = tracker.window_with(bars_now["BTCUSDT"], IMPULSE_BARS)
        if (
            len(window) < IMPULSE_BARS
            or btc.atr is None
            or btc.flow_z is None
            or btc.volume_z is None
            or btc.body_atr is None
        ):
            self.rejections["insufficient_history"] += 1
            return None
        origin = window[0].open
        impulse_price = window[-1].close - origin
        if impulse_price == 0.0:
            self.rejections["not_btc_impulse"] += 1
            return None
        side = Side.LONG if impulse_price > 0.0 else Side.SHORT
        impulse_atr = abs(impulse_price) / btc.atr
        efficiency = self._path_efficiency(window)
        valid = (
            impulse_atr >= 1.25
            and efficiency >= 0.72
            and side.sign * btc.flow_z >= 0.90
            and btc.volume_z >= 0.80
            and side.sign * btc.body_atr >= 0.30
        )
        if not valid:
            self.rejections["not_btc_impulse"] += 1
            return None
        return side, origin, impulse_atr, efficiency

    def _open_laggards(
        self,
        *,
        snapshots: dict[str, Snapshot],
        bars_now: dict[str, AuctionBar],
        side: Side,
        btc_origin: float,
        btc_impulse_atr: float,
        btc_efficiency: float,
    ) -> None:
        btc = snapshots["BTCUSDT"]
        candidates: list[tuple[str, float, float, float, float]] = []
        for symbol in TARGETS:
            snapshot = snapshots[symbol]
            window = self.trackers[symbol].window_with(bars_now[symbol], IMPULSE_BARS)
            if len(window) < IMPULSE_BARS or snapshot.atr is None or snapshot.atr <= 0.0:
                continue
            alt_origin = window[0].open
            alt_move_atr = side.sign * (window[-1].close - alt_origin) / snapshot.atr
            expected = 0.65 * btc_impulse_atr
            remaining = expected - alt_move_atr
            if remaining >= 1.00 and alt_move_atr >= -0.50:
                candidates.append((symbol, remaining, alt_origin, snapshot.atr, alt_move_atr))
        if not candidates:
            self.rejections["no_laggard"] += 1
            return
        deepest_symbol = max(candidates, key=lambda row: row[1])[0]
        self.event_counter += 1
        event_id = f"btc-lead:{self.event_counter}:{bars_now['BTCUSDT'].ts_event_ns}"
        for symbol, remaining, alt_origin, alt_atr, alt_move in candidates:
            self.pending[symbol] = LagState(
                event_id=event_id,
                symbol=symbol,
                side=side,
                signal_index=self.index,
                expiry_index=self.index + 6,
                btc_origin=btc_origin,
                btc_end=bars_now["BTCUSDT"].close,
                btc_impulse_atr=btc_impulse_atr,
                btc_impulse_price=bars_now["BTCUSDT"].close - btc_origin,
                alt_origin=alt_origin,
                alt_atr=alt_atr,
                expected_alt_move_atr=0.65 * btc_impulse_atr,
                initial_alt_move_atr=alt_move,
                initial_remaining_atr=remaining,
                deepest=symbol == deepest_symbol,
                high_impulse=btc_impulse_atr >= 2.00,
                confirmation_extreme=(
                    bars_now[symbol].low if side is Side.LONG else bars_now[symbol].high
                ),
            )
        self.cooldown_until = self.index + 8

    @staticmethod
    def _minimum_stop(entry: float, stop: float, side: Side, atr: float) -> float:
        minimum = 0.75 * atr
        return min(stop, entry - minimum) if side is Side.LONG else max(stop, entry + minimum)

    def _emit(
        self,
        *,
        rule: str,
        state: LagState,
        target_snapshot: Snapshot,
        btc_snapshot: Snapshot,
        remaining_atr: float,
        hold_fraction: float,
        structure_break: bool,
    ) -> Pending | None:
        if not structure_break or target_snapshot.atr is None:
            return None
        side = state.side
        entry = target_snapshot.bar.close
        raw_stop = (
            state.confirmation_extreme - 0.10 * state.alt_atr
            if side is Side.LONG
            else state.confirmation_extreme + 0.10 * state.alt_atr
        )
        stop = self._minimum_stop(entry, raw_stop, side, state.alt_atr)
        target = entry + side.sign * remaining_atr * state.alt_atr
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
            f"leader-lag:{rule}:{state.event_id}:{state.symbol}:{side.value.lower()}:{target_snapshot.bar.ts_event_ns}"
        )
        plan = TradePlan(
            scenario_id=scenario_id,
            side=side,
            response=Response.ACCEPTANCE_FAILURE,
            signal_time_ns=target_snapshot.bar.ts_event_ns,
            observed_time_ns=target_snapshot.bar.ts_event_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            anchor_high=max(state.alt_origin, entry),
            anchor_low=min(state.alt_origin, entry),
            sweep_extreme=state.confirmation_extreme,
            atr=state.alt_atr,
            estimated_reward_risk=rr,
            max_hold_bars=90,
            reason_code="BTC_LEADER_LAG_RELEASE_CONFIRMED",
        )
        pending = Pending(symbol=state.symbol, horizon=60, plan=plan)
        self.schedules[rule].setdefault(target_snapshot.bar.ts_event_ns, []).append(pending)
        self.evidence.append(
            Evidence(
                rule=rule,
                scenario_id=scenario_id,
                event_id=state.event_id,
                signal_time_ns=target_snapshot.bar.ts_event_ns,
                symbol=state.symbol,
                side=side.value,
                btc_impulse_atr=state.btc_impulse_atr,
                btc_path_efficiency=abs(state.btc_impulse_price) / max(abs(state.btc_impulse_price), 1e-12),
                btc_flow_z=side.sign * float(btc_snapshot.flow_z or 0.0),
                btc_volume_z=float(btc_snapshot.volume_z or 0.0),
                initial_alt_move_atr=state.initial_alt_move_atr,
                initial_remaining_atr=state.initial_remaining_atr,
                confirmation_remaining_atr=remaining_atr,
                btc_hold_fraction=hold_fraction,
                btc_confirmation_flow_z=side.sign * float(btc_snapshot.flow_z or 0.0),
                alt_confirmation_flow_z=side.sign * float(target_snapshot.flow_z or 0.0),
                alt_confirmation_body_atr=side.sign * float(target_snapshot.body_atr or 0.0),
                deepest_at_signal=state.deepest,
                stop_distance_atr=abs(entry - stop) / state.alt_atr,
                target_distance_atr=abs(target - entry) / state.alt_atr,
            ),
        )
        return pending

    def _confirm_pending(self, snapshots: dict[str, Snapshot]) -> None:
        btc = snapshots["BTCUSDT"]
        for symbol, state in list(self.pending.items()):
            target = snapshots[symbol]
            if self.index > state.expiry_index:
                self.rejections["expired_laggard"] += 1
                del self.pending[symbol]
                continue
            if self.index <= state.signal_index or target.atr is None:
                continue
            if state.side is Side.LONG:
                state.confirmation_extreme = min(state.confirmation_extreme, target.bar.low)
            else:
                state.confirmation_extreme = max(state.confirmation_extreme, target.bar.high)
            btc_progress = state.side.sign * (btc.bar.close - state.btc_origin)
            hold_fraction = btc_progress / max(abs(state.btc_impulse_price), 1e-12)
            if hold_fraction < 0.40:
                self.rejections["leader_retraced"] += 1
                del self.pending[symbol]
                continue
            alt_move_atr = state.side.sign * (
                target.bar.close - state.alt_origin
            ) / state.alt_atr
            remaining = state.expected_alt_move_atr - alt_move_atr
            if remaining < 0.75:
                del self.pending[symbol]
                continue
            flow_ok = (
                target.flow_z is not None
                and state.side.sign * target.flow_z >= 0.40
            )
            body_ok = (
                target.body_atr is not None
                and state.side.sign * target.body_atr >= 0.15
            )
            structure_break = (
                target.prior_structure_high is not None
                and target.bar.close > target.prior_structure_high
                if state.side is Side.LONG
                else target.prior_structure_low is not None
                and target.bar.close < target.prior_structure_low
            )
            if not (flow_ok and body_ok and structure_break):
                continue
            base = self._emit(
                rule="btc-leader-all-lag-release",
                state=state,
                target_snapshot=target,
                btc_snapshot=btc,
                remaining_atr=remaining,
                hold_fraction=hold_fraction,
                structure_break=structure_break,
            )
            persistent = (
                hold_fraction >= 0.60
                and btc.flow_z is not None
                and state.side.sign * btc.flow_z >= 0.0
            )
            if persistent:
                self._emit(
                    rule="btc-leader-persistent-release",
                    state=state,
                    target_snapshot=target,
                    btc_snapshot=btc,
                    remaining_atr=remaining,
                    hold_fraction=hold_fraction,
                    structure_break=structure_break,
                )
                if state.deepest:
                    self._emit(
                        rule="btc-leader-deepest-persistent",
                        state=state,
                        target_snapshot=target,
                        btc_snapshot=btc,
                        remaining_atr=remaining,
                        hold_fraction=hold_fraction,
                        structure_break=structure_break,
                    )
            if state.high_impulse:
                self._emit(
                    rule="btc-leader-high-impulse",
                    state=state,
                    target_snapshot=target,
                    btc_snapshot=btc,
                    remaining_atr=remaining,
                    hold_fraction=hold_fraction,
                    structure_break=structure_break,
                )
            if base is not None:
                del self.pending[symbol]

    def on_bars(self, bars_now: dict[str, AuctionBar]) -> None:
        self.index += 1
        snapshots = {
            symbol: self.trackers[symbol].snapshot(bars_now[symbol])
            for symbol in SYMBOLS
        }
        self._confirm_pending(snapshots)
        if self.index <= self.cooldown_until:
            self.rejections["cooldown"] += 1
        else:
            impulse = self._detect_btc_impulse(snapshots=snapshots, bars_now=bars_now)
            if impulse is not None:
                side, origin, impulse_atr, efficiency = impulse
                self._open_laggards(
                    snapshots=snapshots,
                    bars_now=bars_now,
                    side=side,
                    btc_origin=origin,
                    btc_impulse_atr=impulse_atr,
                    btc_efficiency=efficiency,
                )
        for symbol in SYMBOLS:
            self.trackers[symbol].update(bars_now[symbol])


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


def load_segment(
    *, start: datetime, end: datetime, cache: Path
) -> tuple[dict[str, list[AuctionBar]], list[dict[str, Any]]]:
    bars: dict[str, list[AuctionBar]] = {}
    records: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frame, downloaded = load_interval(
            symbol=symbol,
            start=start,
            end=end,
            cache_dir=cache / symbol,
            warmup_minutes=720,
        )
        bars[symbol] = to_auction_bars(frame)
        records.extend(asdict(record) for record in downloaded)
    return bars, records


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
    manifest: list[dict[str, Any]] = []
    plan_counts: dict[str, dict[str, int]] = {}

    for label, start, end in week_segments(research):
        bars_by_symbol, records = load_segment(start=start, end=end, cache=args.cache)
        manifest.extend(records)
        maps = {
            symbol: {bar.ts_event_ns: bar for bar in bars}
            for symbol, bars in bars_by_symbol.items()
        }
        timestamps = sorted(set.intersection(*(set(mapping) for mapping in maps.values())))
        detector = LeaderLagDetector(candidate)
        for ts_ns in timestamps:
            detector.on_bars({symbol: maps[symbol][ts_ns] for symbol in SYMBOLS})
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
            trades, metrics, daily = simulate(
                variant=Variant(rule, TARGETS, (60,)),
                bars_by_symbol=bars_by_symbol,
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
            atomic_json(destination / "metrics.json", metrics)
            for risk, rows in daily.items():
                pd.DataFrame(rows).to_csv(
                    destination / f"daily_nav_{risk:.4f}.csv",
                    index=False,
                )
            aggregate_rows[rule].append(metrics)

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

    files = pd.DataFrame(manifest).drop_duplicates(["symbol", "month"])
    atomic_json(
        output / "download_manifest.json",
        {"provider": "Binance Vision", "records": files.to_dict(orient="records")},
    )
    summary = {
        "scenario": "BTC price-discovery lead and alt lag release",
        "leader": "BTCUSDT",
        "targets": list(TARGETS),
        "long_evaluation_run": False,
        "one_global_position": True,
        "one_bar_execution_delay": True,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "plan_counts": plan_counts,
        "aggregates": aggregates,
    }
    atomic_json(output / "leader_lag_release_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-leader-lag-release",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-leader-lag-release",
    )
    parser.add_argument(
        "--risk-rates",
        default=",".join(str(value) for value in RISK_RATES),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
