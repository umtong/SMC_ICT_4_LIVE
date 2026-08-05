#!/usr/bin/env python3
"""Causal cross-asset SMT liquidity-divergence portfolio probe.

The existing failed-auction state machine remains the event detector.  This
scenario classifies an altcoin sweep as either a market-wide value relocation
or an isolated liquidity collection by comparing the same completed 4-hour
edge across BTC, ETH, SOL and XRP.  Only information available by the target's
completed displacement bar is used; execution remains one completed bar later.
Portfolio accounting, fees, structural loss sizing and the one-global-position
constraint are delegated to the shared candidate-01 portfolio probe.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import statistics
import sys
from typing import Any, Deque, Iterable

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from core import AuctionBar, AuctionStateMachine, CandidateConfig, Side  # noqa: E402
from data import DownloadRecord, load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from portfolio_probe import Variant, _aggregate_variant, simulate  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TARGETS = ("ETHUSDT", "SOLUSDT", "XRPUSDT")
NS_PER_MINUTE = 60_000_000_000
RISK_RATES = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)
RULES = (
    "all-alt-failed-auctions",
    "isolated-sweep",
    "isolated-btc-reversal",
    "isolated-basket-reversal",
    "isolated-strong-target-release",
    "systemic-sweep-reversal",
    "smt-composite",
)


@dataclass(slots=True)
class Block:
    block_id: int
    high: float
    low: float
    bars: int = 1

    def update(self, bar: AuctionBar) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.bars += 1


@dataclass(frozen=True, slots=True)
class Snapshot:
    bar: AuctionBar
    anchor_high: float | None
    anchor_low: float | None
    atr: float | None
    flow_z: float | None
    body_atr: float | None


class Tracker:
    """Completed-range and normalized flow state with no current-bar leakage."""

    def __init__(self, config: CandidateConfig) -> None:
        self.config = config
        self.range_ns = config.range_minutes * NS_PER_MINUTE
        self.block: Block | None = None
        self.anchor_high: float | None = None
        self.anchor_low: float | None = None
        self.true_ranges: Deque[float] = deque(maxlen=config.atr_lookback)
        self.flows: Deque[float] = deque(maxlen=config.flow_lookback)
        self.previous_close: float | None = None

    @staticmethod
    def zscore(value: float, history: Iterable[float]) -> float | None:
        rows = list(history)
        if len(rows) < 20:
            return None
        mean = statistics.fmean(rows)
        variance = statistics.fmean((item - mean) ** 2 for item in rows)
        return 0.0 if variance <= 0.0 else (value - mean) / variance**0.5

    def on_bar(self, bar: AuctionBar) -> Snapshot:
        atr = statistics.fmean(self.true_ranges) if len(self.true_ranges) >= 20 else None
        flow_z = self.zscore(bar.aggressive_imbalance, self.flows)
        body_atr = (bar.close - bar.open) / atr if atr is not None and atr > 0.0 else None

        block_id = bar.ts_event_ns // self.range_ns
        if self.block is None:
            self.block = Block(block_id, bar.high, bar.low)
        elif block_id != self.block.block_id:
            minimum = int(self.config.range_minutes * self.config.min_anchor_fraction)
            if self.block.bars >= minimum:
                self.anchor_high = self.block.high
                self.anchor_low = self.block.low
            self.block = Block(block_id, bar.high, bar.low)
        else:
            self.block.update(bar)

        snapshot = Snapshot(
            bar=bar,
            anchor_high=self.anchor_high,
            anchor_low=self.anchor_low,
            atr=atr,
            flow_z=flow_z,
            body_atr=body_atr,
        )
        previous = self.previous_close
        true_range = (
            bar.high - bar.low
            if previous is None
            else max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))
        )
        self.true_ranges.append(true_range)
        self.flows.append(bar.aggressive_imbalance)
        self.previous_close = bar.close
        return snapshot


@dataclass(frozen=True, slots=True)
class Probe:
    scenario_id: str
    target: str
    side: Side
    target_excursion_atr: float
    btc_excursion_atr: float
    peer_confirmation_fraction: float
    alt_confirmation_fraction: float


@dataclass(frozen=True, slots=True)
class Feature:
    scenario_id: str
    symbol: str
    side: str
    target_excursion_atr: float
    btc_same_edge_excursion_atr: float
    peer_confirmation_fraction: float
    alt_confirmation_fraction: float
    target_reversal_flow_z: float
    target_reversal_body_atr: float
    btc_reversal_flow_z: float
    btc_reversal_body_atr: float
    peer_median_reversal_flow_z: float
    peer_median_reversal_body_atr: float
    peer_reversal_fraction: float
    peer_joint_reversal_fraction: float
    isolation_score: float


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def segments(research: dict[str, Any]) -> list[tuple[str, datetime, datetime, str]]:
    def week(label: str, value: str, role: str) -> tuple[str, datetime, datetime, str]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7), role

    return [
        week("discovery", str(research["discovery_week"]), "quick"),
        *[
            week(f"confirmation-{index + 1}", value, "quick")
            for index, value in enumerate(research["confirmation_weeks"])
        ],
        *[
            week(f"untouched-{index + 1}", value, "untouched")
            for index, value in enumerate(research.get("additional_random_weeks", []))
        ],
        (
            "long-evaluation",
            parse_utc_date(str(research["long_start"])),
            parse_utc_date(str(research["long_end"])),
            "development",
        ),
    ]


def load_segment(
    *, start: datetime, end: datetime, cache: Path, warmup: int
) -> tuple[dict[str, list[AuctionBar]], list[DownloadRecord]]:
    result: dict[str, list[AuctionBar]] = {}
    records: list[DownloadRecord] = []
    for symbol in SYMBOLS:
        frame, downloaded = load_interval(
            symbol=symbol,
            start=start,
            end=end,
            cache_dir=cache / symbol,
            warmup_minutes=warmup,
        )
        result[symbol] = to_auction_bars(frame)
        records.extend(downloaded)
    return result, records


def side_from_id(value: str) -> Side:
    suffix = value.rsplit(":", 1)[-1].upper()
    if suffix == "LONG":
        return Side.LONG
    if suffix == "SHORT":
        return Side.SHORT
    raise ValueError(f"cannot infer side from {value}")


def edge_excursion(snapshot: Snapshot, side: Side) -> float | None:
    if (
        snapshot.anchor_high is None
        or snapshot.anchor_low is None
        or snapshot.atr is None
        or snapshot.atr <= 0.0
    ):
        return None
    return (
        (snapshot.anchor_low - snapshot.bar.low) / snapshot.atr
        if side is Side.LONG
        else (snapshot.bar.high - snapshot.anchor_high) / snapshot.atr
    )


def capture_probe(
    *, event: Any, target: str, snapshots: dict[str, Snapshot]
) -> Probe | None:
    side = side_from_id(event.scenario_id)
    excursions: dict[str, float] = {}
    for symbol, snapshot in snapshots.items():
        value = edge_excursion(snapshot, side)
        if value is None:
            return None
        excursions[symbol] = value
    peers = [symbol for symbol in SYMBOLS if symbol != target]
    alt_peers = [symbol for symbol in TARGETS if symbol != target]
    target_excursion = abs(
        float(event.details["excursion_extreme"]) - float(event.details["boundary"])
    ) / float(event.details["atr"])
    return Probe(
        scenario_id=event.scenario_id,
        target=target,
        side=side,
        target_excursion_atr=target_excursion,
        btc_excursion_atr=excursions["BTCUSDT"],
        peer_confirmation_fraction=statistics.fmean(
            1.0 if excursions[symbol] >= 0.03 else 0.0 for symbol in peers
        ),
        alt_confirmation_fraction=statistics.fmean(
            1.0 if excursions[symbol] >= 0.03 else 0.0 for symbol in alt_peers
        ),
    )


def make_feature(
    *, scenario_id: str, target: str, probe: Probe, snapshots: dict[str, Snapshot]
) -> Feature | None:
    sign = float(probe.side.sign)
    flow: dict[str, float] = {}
    body: dict[str, float] = {}
    for symbol, snapshot in snapshots.items():
        if snapshot.flow_z is None or snapshot.body_atr is None:
            return None
        flow[symbol] = sign * snapshot.flow_z
        body[symbol] = sign * snapshot.body_atr
    peers = [symbol for symbol in SYMBOLS if symbol != target]
    return Feature(
        scenario_id=scenario_id,
        symbol=target,
        side=probe.side.value,
        target_excursion_atr=probe.target_excursion_atr,
        btc_same_edge_excursion_atr=probe.btc_excursion_atr,
        peer_confirmation_fraction=probe.peer_confirmation_fraction,
        alt_confirmation_fraction=probe.alt_confirmation_fraction,
        target_reversal_flow_z=flow[target],
        target_reversal_body_atr=body[target],
        btc_reversal_flow_z=flow["BTCUSDT"],
        btc_reversal_body_atr=body["BTCUSDT"],
        peer_median_reversal_flow_z=float(statistics.median(flow[symbol] for symbol in peers)),
        peer_median_reversal_body_atr=float(statistics.median(body[symbol] for symbol in peers)),
        peer_reversal_fraction=statistics.fmean(
            1.0 if body[symbol] > 0.0 else 0.0 for symbol in peers
        ),
        peer_joint_reversal_fraction=statistics.fmean(
            1.0 if body[symbol] > 0.0 and flow[symbol] > 0.0 else 0.0
            for symbol in peers
        ),
        isolation_score=(
            probe.target_excursion_atr
            - max(probe.btc_excursion_atr, 0.0)
            - probe.peer_confirmation_fraction
        ),
    )


def extract_features(
    *,
    bars_by_symbol: dict[str, list[AuctionBar]],
    candidate: CandidateConfig,
    start: datetime,
    end: datetime,
) -> tuple[pd.DataFrame, dict[str, int]]:
    start_ns = int(pd.Timestamp(start).value)
    end_ns = int(pd.Timestamp(end).value)
    maps = {
        symbol: {bar.ts_event_ns: bar for bar in bars}
        for symbol, bars in bars_by_symbol.items()
    }
    timestamps = sorted({value for mapping in maps.values() for value in mapping})
    trackers = {symbol: Tracker(candidate) for symbol in SYMBOLS}
    machines = {
        symbol: AuctionStateMachine(candidate, instrument_id=f"{symbol}-PERP.BINANCE:240m")
        for symbol in TARGETS
    }
    cursors = {symbol: 0 for symbol in TARGETS}
    probes: dict[str, Probe] = {}
    features: list[Feature] = []
    diagnostic = {"plans": 0, "missing_probe": 0, "missing_context": 0}

    for ts_ns in timestamps:
        bars_now = {
            symbol: mapping[ts_ns]
            for symbol, mapping in maps.items()
            if ts_ns in mapping
        }
        if set(bars_now) != set(SYMBOLS):
            continue
        snapshots = {
            symbol: trackers[symbol].on_bar(bars_now[symbol])
            for symbol in SYMBOLS
        }
        for target in TARGETS:
            machine = machines[target]
            plan = machine.on_bar(bars_now[target])
            transitions = machine.transitions[cursors[target] :]
            cursors[target] = len(machine.transitions)
            for event in transitions:
                if event.event_type == "LIQUIDITY_PROBE_REJECTED":
                    probe = capture_probe(event=event, target=target, snapshots=snapshots)
                    if probe is None:
                        diagnostic["missing_context"] += 1
                    else:
                        probes[event.scenario_id] = probe
            if plan is None or not (start_ns <= ts_ns < end_ns):
                continue
            diagnostic["plans"] += 1
            probe = probes.get(plan.scenario_id)
            if probe is None:
                diagnostic["missing_probe"] += 1
                continue
            feature = make_feature(
                scenario_id=plan.scenario_id,
                target=target,
                probe=probe,
                snapshots=snapshots,
            )
            if feature is None:
                diagnostic["missing_context"] += 1
            else:
                features.append(feature)
    return pd.DataFrame(asdict(feature) for feature in features), diagnostic


def rule_mask(frame: pd.DataFrame, rule: str) -> pd.Series:
    isolated = (
        (frame["btc_same_edge_excursion_atr"] < 0.03)
        & (frame["peer_confirmation_fraction"] <= 1.0 / 3.0 + 1e-12)
    )
    btc_reversal = (
        (frame["btc_reversal_body_atr"] >= 0.08)
        & (frame["btc_reversal_flow_z"] >= 0.0)
    )
    basket_reversal = (
        (frame["peer_reversal_fraction"] >= 2.0 / 3.0 - 1e-12)
        & (frame["peer_median_reversal_body_atr"] >= 0.05)
        & (frame["peer_median_reversal_flow_z"] >= 0.0)
    )
    strong_target = (
        (frame["target_reversal_flow_z"] >= 0.75)
        & (frame["target_reversal_body_atr"] >= 0.38)
    )
    systemic = (
        (frame["peer_confirmation_fraction"] >= 2.0 / 3.0 - 1e-12)
        & (frame["peer_joint_reversal_fraction"] >= 2.0 / 3.0 - 1e-12)
    )
    if rule == "all-alt-failed-auctions":
        return pd.Series(True, index=frame.index)
    if rule == "isolated-sweep":
        return isolated
    if rule == "isolated-btc-reversal":
        return isolated & btc_reversal
    if rule == "isolated-basket-reversal":
        return isolated & basket_reversal
    if rule == "isolated-strong-target-release":
        return isolated & strong_target
    if rule == "systemic-sweep-reversal":
        return systemic
    if rule == "smt-composite":
        return isolated & (btc_reversal | basket_reversal | strong_target)
    raise ValueError(rule)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    risk_rates = tuple(float(value) for value in args.risk_rates.split(","))
    warmup = max(int(research.get("warmup_minutes", 420)), candidate.range_minutes + 180)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    role_rows = {
        role: {rule: [] for rule in RULES}
        for role in ("quick", "untouched", "development")
    }
    manifests: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}

    for label, start, end, role in segments(research):
        bars, records = load_segment(start=start, end=end, cache=args.cache, warmup=warmup)
        manifests.extend(asdict(record) for record in records)
        features, diagnostic = extract_features(
            bars_by_symbol=bars,
            candidate=candidate,
            start=start,
            end=end,
        )
        diagnostics[label] = {**diagnostic, "features": int(len(features))}
        features.to_csv(output / f"{label}_features.csv", index=False)
        for rule in RULES:
            selected = features.loc[rule_mask(features, rule)]
            allowed = frozenset(selected["scenario_id"].astype(str))
            trades, metrics, daily = simulate(
                variant=Variant(rule, TARGETS, (candidate.range_minutes,)),
                bars_by_symbol=bars,
                evaluation_start=start,
                evaluation_end=end,
                base_candidate=candidate,
                cost=cost,
                minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
                minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
                starting_nav=float(execution["starting_nav"]),
                risk_rates=risk_rates,
                allowed_scenario_ids=allowed,
            )
            destination = output / rule / label
            destination.mkdir(parents=True, exist_ok=True)
            if not trades.empty:
                trades = trades.merge(features, on="scenario_id", how="left", validate="one_to_one")
            trades.to_csv(destination / "trades.csv", index=False)
            atomic_json(destination / "metrics.json", metrics)
            for risk, rows in daily.items():
                pd.DataFrame(rows).to_csv(destination / f"daily_nav_{risk:.4f}.csv", index=False)
            role_rows[role][rule].append(metrics)

    aggregates: dict[str, Any] = {}
    for role, rules in role_rows.items():
        aggregates[role] = {}
        for rule, rows in rules.items():
            aggregate = _aggregate_variant(rows, risk_rates)
            aggregates[role][rule] = aggregate
            atomic_json(output / rule / f"{role}_aggregate.json", aggregate)

    files = pd.DataFrame(manifests).drop_duplicates(["symbol", "month"])
    atomic_json(
        output / "download_manifest.json",
        {"provider": "Binance Vision", "records": files.to_dict(orient="records")},
    )
    summary = {
        "scenario": "cross-asset SMT liquidity divergence",
        "leader": "BTCUSDT",
        "targets": list(TARGETS),
        "one_global_position": True,
        "one_bar_execution_delay": True,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "diagnostics": diagnostics,
        "aggregates": aggregates,
    }
    atomic_json(output / "smt_divergence_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-smt-divergence",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-smt-divergence",
    )
    parser.add_argument(
        "--risk-rates",
        default=",".join(str(value) for value in RISK_RATES),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
