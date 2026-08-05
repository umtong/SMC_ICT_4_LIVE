#!/usr/bin/env python3
"""First-week probe for repeated liquidity pools with true one-minute MSS.

The external liquidity event remains a causally confirmed repeated five-minute
swing pool sweep. The prior candidate incorrectly used a five-minute swing as
the lower-timeframe CHoCH threshold. This probe freezes the most recent
causally confirmed one-minute opposing pivot at the sweep, then requires its
break before considering entry.

Four controlled execution variants share the same pool, sweep, target, costs,
risk sizing and single-position accounting:

* ltf-mss-market: first directional one-minute MSS, next-minute market entry;
* ltf-mss-flow-market: MSS plus moderate displacement and flow;
* ltf-mss-fvg-retest: the same displaced MSS creates a strict 1m FVG and price
  revisits it before entry;
* ltf-mss-body-retest: the same displaced MSS revisits the lower/upper half of
  its causal displacement body before entry.

Only the first frozen BTC week is run. Variants are diagnostic controls; none is
promoted unless trade frequency, win rate and cost-adjusted NAV growth are
naturally compatible with the project objective.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any, Literal

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from core import AuctionBar, CandidateConfig, Response, Side, TradePlan  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from hybrid_resting_pool_probe import MinuteState, MinuteTracker, ONE_MINUTE_NS, SweepOnlyStructure  # noqa: E402
from portfolio_probe import Pending, Variant, simulate  # noqa: E402
from resting_liquidity_pool_probe import SweepAttempt, aggregate_five_minute  # noqa: E402


RULES = (
    "ltf-mss-market",
    "ltf-mss-flow-market",
    "ltf-mss-fvg-retest",
    "ltf-mss-body-retest",
)
RISK_RATE = 0.03
MAX_PIVOT_AGE_MINUTES = 30
MSS_EXPIRY_MINUTES = 15
RETEST_EXPIRY_MINUTES = 8


@dataclass(frozen=True, slots=True)
class MinutePivot:
    side: Literal["HIGH", "LOW"]
    price: float
    time_ns: int
    bar_index: int
    atr: float


class MinuteStructure:
    """Causal 2-left/2-right one-minute pivots plus pre-bar statistics."""

    def __init__(self, config: CandidateConfig) -> None:
        self.stats = MinuteTracker(config)
        self.bars: list[AuctionBar] = []
        self.states: list[MinuteState] = []
        self.latest_high: MinutePivot | None = None
        self.latest_low: MinutePivot | None = None

    def observe(self, bar: AuctionBar) -> MinuteState:
        state = self.stats.observe(bar)
        self.bars.append(bar)
        self.states.append(state)
        if len(self.bars) >= 5:
            index = len(self.bars) - 3
            candidate = self.bars[index]
            candidate_state = self.states[index]
            if candidate_state.atr is not None and candidate_state.atr > 0.0:
                left = self.bars[index - 2 : index]
                right = self.bars[index + 1 : index + 3]
                high = (
                    all(candidate.high > item.high for item in left)
                    and all(candidate.high >= item.high for item in right)
                )
                low = (
                    all(candidate.low < item.low for item in left)
                    and all(candidate.low <= item.low for item in right)
                )
                if high and not low:
                    self.latest_high = MinutePivot(
                        side="HIGH",
                        price=candidate.high,
                        time_ns=candidate.ts_event_ns,
                        bar_index=index,
                        atr=candidate_state.atr,
                    )
                elif low and not high:
                    self.latest_low = MinutePivot(
                        side="LOW",
                        price=candidate.low,
                        time_ns=candidate.ts_event_ns,
                        bar_index=index,
                        atr=candidate_state.atr,
                    )
        self.stats.update(bar)
        return state


@dataclass(slots=True)
class ActiveSweep:
    source: SweepAttempt
    internal_break: float
    pivot_time_ns: int
    sweep_time_ns: int
    expiry_time_ns: int
    sweep_extreme: float


@dataclass(slots=True)
class RetestSetup:
    rule: str
    source: SweepAttempt
    side: Side
    mss_time_ns: int
    expiry_time_ns: int
    sweep_extreme: float
    zone_low: float
    zone_high: float


@dataclass(frozen=True, slots=True)
class MssEvent:
    sweep_time_ns: int
    mss_time_ns: int
    side: str
    source_pool_id: int
    target_pool_id: int
    pivot_time_ns: int
    pivot_age_minutes: int
    latency_minutes: int
    internal_break: float
    mss_close: float
    aligned_flow_z: float
    aligned_body_atr: float
    volume_z: float
    sweep_extreme: float
    stop_distance_atr_5m: float
    target_distance_atr_5m: float
    raw_reward_risk: float
    strict_fvg: bool
    immediate_rules_emitted: str


@dataclass(frozen=True, slots=True)
class RetestEvent:
    rule: str
    mss_time_ns: int
    signal_time_ns: int
    side: str
    source_pool_id: int
    zone_low: float
    zone_high: float
    entry_close: float
    emitted: bool
    rejection: str


class LtfMssDetector:
    def __init__(self, config: CandidateConfig, *, evaluation_start_ns: int) -> None:
        self.config = config
        self.evaluation_start_ns = evaluation_start_ns
        self.pool_structure = SweepOnlyStructure(config)
        self.minute = MinuteStructure(config)
        self.active: ActiveSweep | None = None
        self.retests: list[RetestSetup] = []
        self.schedules: dict[str, dict[int, list[Pending]]] = {
            rule: {} for rule in RULES
        }
        self.stage_counts: Counter[str] = Counter()
        self.rule_counts: dict[str, Counter[str]] = {
            rule: Counter() for rule in RULES
        }
        self.mss_events: list[MssEvent] = []
        self.retest_events: list[RetestEvent] = []

    def on_five_minute(self, bar: AuctionBar) -> None:
        self.pool_structure.on_bar(bar)
        attempt = self.pool_structure.attempt
        if attempt is None:
            return
        self.pool_structure.attempt = None
        if bar.ts_event_ns < self.evaluation_start_ns:
            self.active = None
            return
        self.stage_counts["pool_sweeps"] += 1
        pivot = (
            self.minute.latest_high
            if attempt.side is Side.LONG
            else self.minute.latest_low
        )
        if pivot is None:
            self.stage_counts["no_ltf_pivot"] += 1
            return
        age_minutes = int((bar.ts_event_ns - pivot.time_ns) // ONE_MINUTE_NS)
        if age_minutes < 0 or age_minutes > MAX_PIVOT_AGE_MINUTES:
            self.stage_counts["stale_ltf_pivot"] += 1
            return
        correctly_placed = (
            pivot.price > bar.close
            if attempt.side is Side.LONG
            else pivot.price < bar.close
        )
        if not correctly_placed:
            self.stage_counts["ltf_pivot_already_broken"] += 1
            return
        if self.active is not None:
            self.stage_counts["active_sweep_replaced"] += 1
        self.active = ActiveSweep(
            source=attempt,
            internal_break=pivot.price,
            pivot_time_ns=pivot.time_ns,
            sweep_time_ns=bar.ts_event_ns,
            expiry_time_ns=bar.ts_event_ns + MSS_EXPIRY_MINUTES * ONE_MINUTE_NS,
            sweep_extreme=attempt.sweep_extreme,
        )
        self.stage_counts["armed_ltf_sweeps"] += 1

    @staticmethod
    def _structural_stop(source: SweepAttempt, sweep_extreme: float) -> float:
        buffer = 0.10 * source.atr
        return (
            sweep_extreme - buffer
            if source.side is Side.LONG
            else sweep_extreme + buffer
        )

    def _build_plan(
        self,
        *,
        rule: str,
        signal_bar: AuctionBar,
        source: SweepAttempt,
        sweep_extreme: float,
        reason_code: str,
    ) -> tuple[Pending | None, str, float, float, float]:
        side = source.side
        entry = signal_bar.close
        stop = self._structural_stop(source, sweep_extreme)
        target = source.target_level
        if side is Side.LONG and signal_bar.high >= target:
            return None, "target_touched", stop, target, 0.0
        if side is Side.SHORT and signal_bar.low <= target:
            return None, "target_touched", stop, target, 0.0
        geometry = stop < entry < target if side is Side.LONG else target < entry < stop
        if not geometry:
            return None, "invalid_geometry", stop, target, 0.0
        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = reward / risk if risk > 0.0 else 0.0
        if rr <= 1.0:
            return None, "raw_reward_risk_not_positive", stop, target, rr
        scenario_id = (
            f"ltf-mss-pool:{rule}:{source.source_pool_id}:{source.target_pool_id}:"
            f"{side.value.lower()}:{signal_bar.ts_event_ns}"
        )
        plan = TradePlan(
            scenario_id=scenario_id,
            side=side,
            response=Response.SWEEP_FAILURE,
            signal_time_ns=signal_bar.ts_event_ns,
            observed_time_ns=signal_bar.ts_event_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            anchor_high=max(source.source_level, source.target_level),
            anchor_low=min(source.source_level, source.target_level),
            sweep_extreme=sweep_extreme,
            atr=source.atr,
            estimated_reward_risk=rr,
            max_hold_bars=180,
            reason_code=reason_code,
        )
        pending = Pending(symbol="BTCUSDT", horizon=60, plan=plan)
        self.schedules[rule].setdefault(signal_bar.ts_event_ns, []).append(pending)
        self.rule_counts[rule]["plans_emitted"] += 1
        return pending, "emitted", stop, target, rr

    def _create_retests(
        self,
        *,
        bar: AuctionBar,
        source: SweepAttempt,
        sweep_extreme: float,
    ) -> bool:
        side = source.side
        strict_fvg = False
        if len(self.minute.bars) >= 3:
            two_back = self.minute.bars[-3]
            if side is Side.LONG and bar.low > two_back.high:
                self.retests.append(
                    RetestSetup(
                        rule="ltf-mss-fvg-retest",
                        source=source,
                        side=side,
                        mss_time_ns=bar.ts_event_ns,
                        expiry_time_ns=bar.ts_event_ns + RETEST_EXPIRY_MINUTES * ONE_MINUTE_NS,
                        sweep_extreme=sweep_extreme,
                        zone_low=two_back.high,
                        zone_high=bar.low,
                    ),
                )
                strict_fvg = True
            elif side is Side.SHORT and bar.high < two_back.low:
                self.retests.append(
                    RetestSetup(
                        rule="ltf-mss-fvg-retest",
                        source=source,
                        side=side,
                        mss_time_ns=bar.ts_event_ns,
                        expiry_time_ns=bar.ts_event_ns + RETEST_EXPIRY_MINUTES * ONE_MINUTE_NS,
                        sweep_extreme=sweep_extreme,
                        zone_low=bar.high,
                        zone_high=two_back.low,
                    ),
                )
                strict_fvg = True
        directional_body = (
            bar.close > bar.open if side is Side.LONG else bar.close < bar.open
        )
        if directional_body:
            midpoint = 0.5 * (bar.open + bar.close)
            zone_low = min(bar.open, midpoint)
            zone_high = max(bar.open, midpoint)
            self.retests.append(
                RetestSetup(
                    rule="ltf-mss-body-retest",
                    source=source,
                    side=side,
                    mss_time_ns=bar.ts_event_ns,
                    expiry_time_ns=bar.ts_event_ns + RETEST_EXPIRY_MINUTES * ONE_MINUTE_NS,
                    sweep_extreme=sweep_extreme,
                    zone_low=zone_low,
                    zone_high=zone_high,
                ),
            )
        return strict_fvg

    def _process_retests(self, bar: AuctionBar) -> None:
        remaining: list[RetestSetup] = []
        for setup in self.retests:
            if bar.ts_event_ns <= setup.mss_time_ns:
                remaining.append(setup)
                continue
            if bar.ts_event_ns > setup.expiry_time_ns:
                self.rule_counts[setup.rule]["expired"] += 1
                continue
            stop = self._structural_stop(setup.source, setup.sweep_extreme)
            invalidated = (
                bar.low <= stop if setup.side is Side.LONG else bar.high >= stop
            )
            if invalidated:
                self.rule_counts[setup.rule]["invalidated_before_entry"] += 1
                self.retest_events.append(
                    RetestEvent(
                        rule=setup.rule,
                        mss_time_ns=setup.mss_time_ns,
                        signal_time_ns=bar.ts_event_ns,
                        side=setup.side.value,
                        source_pool_id=setup.source.source_pool_id,
                        zone_low=setup.zone_low,
                        zone_high=setup.zone_high,
                        entry_close=bar.close,
                        emitted=False,
                        rejection="invalidated_before_entry",
                    ),
                )
                continue
            target_touched = (
                bar.high >= setup.source.target_level
                if setup.side is Side.LONG
                else bar.low <= setup.source.target_level
            )
            if target_touched:
                self.rule_counts[setup.rule]["target_touched_before_entry"] += 1
                continue
            touched = bar.low <= setup.zone_high and bar.high >= setup.zone_low
            zone_mid = 0.5 * (setup.zone_low + setup.zone_high)
            rejected_away = (
                bar.close >= zone_mid and bar.close > bar.open
                if setup.side is Side.LONG
                else bar.close <= zone_mid and bar.close < bar.open
            )
            if touched and rejected_away:
                self.rule_counts[setup.rule]["confirmed_retests"] += 1
                pending, reason, _, _, _ = self._build_plan(
                    rule=setup.rule,
                    signal_bar=bar,
                    source=setup.source,
                    sweep_extreme=setup.sweep_extreme,
                    reason_code="LTF_MSS_CAUSAL_ZONE_RETEST_CONFIRMED",
                )
                self.retest_events.append(
                    RetestEvent(
                        rule=setup.rule,
                        mss_time_ns=setup.mss_time_ns,
                        signal_time_ns=bar.ts_event_ns,
                        side=setup.side.value,
                        source_pool_id=setup.source.source_pool_id,
                        zone_low=setup.zone_low,
                        zone_high=setup.zone_high,
                        entry_close=bar.close,
                        emitted=pending is not None,
                        rejection=reason,
                    ),
                )
                if pending is None:
                    self.rule_counts[setup.rule][reason] += 1
                continue
            remaining.append(setup)
        self.retests = remaining

    def on_one_minute(self, bar: AuctionBar) -> None:
        state = self.minute.observe(bar)
        self._process_retests(bar)
        active = self.active
        if active is None or bar.ts_event_ns <= active.sweep_time_ns:
            return
        source = active.source
        side = source.side
        active.sweep_extreme = (
            min(active.sweep_extreme, bar.low)
            if side is Side.LONG
            else max(active.sweep_extreme, bar.high)
        )
        if bar.ts_event_ns > active.expiry_time_ns:
            self.stage_counts["expired_without_ltf_mss"] += 1
            self.active = None
            return
        if state.atr is None or state.flow_z is None or state.body_atr is None or state.volume_z is None:
            self.stage_counts["minute_state_unavailable"] += 1
            return
        mss = (
            bar.close >= active.internal_break + 0.05 * state.atr
            if side is Side.LONG
            else bar.close <= active.internal_break - 0.05 * state.atr
        )
        if not mss:
            return
        self.stage_counts["ltf_mss_events"] += 1
        aligned_flow = side.sign * state.flow_z
        aligned_body = side.sign * state.body_atr
        immediate_emitted: list[str] = []
        if aligned_body >= 0.20 and aligned_flow >= 0.0:
            self.rule_counts["ltf-mss-market"]["eligible_mss"] += 1
            pending, reason, stop, target, rr = self._build_plan(
                rule="ltf-mss-market",
                signal_bar=bar,
                source=source,
                sweep_extreme=active.sweep_extreme,
                reason_code="LTF_MSS_DIRECTIONAL_BREAK_CONFIRMED",
            )
            if pending is not None:
                immediate_emitted.append("ltf-mss-market")
            else:
                self.rule_counts["ltf-mss-market"][reason] += 1
        else:
            stop = self._structural_stop(source, active.sweep_extreme)
            target = source.target_level
            rr = (
                abs(target - bar.close) / abs(bar.close - stop)
                if abs(bar.close - stop) > 0.0
                else 0.0
            )
        displaced = aligned_body >= 0.35 and aligned_flow >= 0.50
        strict_fvg = False
        if displaced:
            self.rule_counts["ltf-mss-flow-market"]["eligible_mss"] += 1
            pending, reason, stop, target, rr = self._build_plan(
                rule="ltf-mss-flow-market",
                signal_bar=bar,
                source=source,
                sweep_extreme=active.sweep_extreme,
                reason_code="LTF_MSS_FLOW_DISPLACEMENT_CONFIRMED",
            )
            if pending is not None:
                immediate_emitted.append("ltf-mss-flow-market")
            else:
                self.rule_counts["ltf-mss-flow-market"][reason] += 1
            strict_fvg = self._create_retests(
                bar=bar,
                source=source,
                sweep_extreme=active.sweep_extreme,
            )
            if strict_fvg:
                self.rule_counts["ltf-mss-fvg-retest"]["setups_created"] += 1
            self.rule_counts["ltf-mss-body-retest"]["setups_created"] += 1
        else:
            self.stage_counts["mss_without_displacement"] += 1
        self.mss_events.append(
            MssEvent(
                sweep_time_ns=active.sweep_time_ns,
                mss_time_ns=bar.ts_event_ns,
                side=side.value,
                source_pool_id=source.source_pool_id,
                target_pool_id=source.target_pool_id,
                pivot_time_ns=active.pivot_time_ns,
                pivot_age_minutes=int(
                    (active.sweep_time_ns - active.pivot_time_ns) // ONE_MINUTE_NS,
                ),
                latency_minutes=int(
                    (bar.ts_event_ns - active.sweep_time_ns) // ONE_MINUTE_NS,
                ),
                internal_break=active.internal_break,
                mss_close=bar.close,
                aligned_flow_z=aligned_flow,
                aligned_body_atr=aligned_body,
                volume_z=state.volume_z,
                sweep_extreme=active.sweep_extreme,
                stop_distance_atr_5m=abs(bar.close - stop) / source.atr,
                target_distance_atr_5m=abs(target - bar.close) / source.atr,
                raw_reward_risk=rr,
                strict_fvg=strict_fvg,
                immediate_rules_emitted="|".join(immediate_emitted),
            ),
        )
        self.active = None


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    start = parse_utc_date(str(research["discovery_week"]))
    end = start + timedelta(days=7)
    start_ns = int(pd.Timestamp(start).as_unit("ns").value)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0

    frame, records = load_interval(
        symbol="BTCUSDT",
        start=start,
        end=end,
        cache_dir=args.cache,
        warmup_minutes=3 * 24 * 60,
    )
    minute_bars = to_auction_bars(frame)
    five_minute_map = {
        bar.ts_event_ns: bar for bar in aggregate_five_minute(frame)
    }
    detector = LtfMssDetector(candidate, evaluation_start_ns=start_ns)
    for minute_bar in minute_bars:
        five_minute_bar = five_minute_map.get(minute_bar.ts_event_ns)
        if five_minute_bar is not None:
            detector.on_five_minute(five_minute_bar)
        detector.on_one_minute(minute_bar)

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(asdict(row) for row in detector.mss_events).to_csv(
        output / "mss_events.csv",
        index=False,
    )
    pd.DataFrame(asdict(row) for row in detector.retest_events).to_csv(
        output / "retest_events.csv",
        index=False,
    )

    rule_results: dict[str, Any] = {}
    for rule in RULES:
        schedule = {
            timestamp: tuple(rows)
            for timestamp, rows in detector.schedules[rule].items()
        }
        trades, metrics, daily = simulate(
            variant=Variant(rule, ("BTCUSDT",), (60,)),
            bars_by_symbol={"BTCUSDT": minute_bars},
            evaluation_start=start,
            evaluation_end=end,
            base_candidate=candidate,
            cost=cost,
            minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
            minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
            starting_nav=float(execution["starting_nav"]),
            risk_rates=(RISK_RATE,),
            allowed_scenario_ids=frozenset(),
            external_plans_by_signal_time=schedule,
        )
        destination = output / rule
        destination.mkdir(parents=True, exist_ok=True)
        trades.to_csv(destination / "trades.csv", index=False)
        atomic_json(destination / "metrics.json", metrics)
        pd.DataFrame(daily[RISK_RATE]).to_csv(
            destination / "daily_nav.csv",
            index=False,
        )
        rule_results[rule] = {
            "stage_counts": dict(detector.rule_counts[rule]),
            "metrics": metrics,
        }

    payload = {
        "scenario": "repeated 5m liquidity-pool sweep with true 1m MSS",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "risk_fraction": RISK_RATE,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "max_pivot_age_minutes": MAX_PIVOT_AGE_MINUTES,
        "mss_expiry_minutes": MSS_EXPIRY_MINUTES,
        "retest_expiry_minutes": RETEST_EXPIRY_MINUTES,
        "stage_counts": dict(detector.stage_counts),
        "rules": rule_results,
        "downloads": [asdict(record) for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "ltf_mss_resting_pool_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-hybrid-first-week",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-ltf-mss-resting-pool",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
