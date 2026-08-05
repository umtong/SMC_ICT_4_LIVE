#!/usr/bin/env python3
"""Causal session-liquidity scenarios for BTC perpetual futures.

Crypto trades continuously, but liquidity and volatility do not arrive
uniformly.  This detector treats 00:00 UTC, 08:00 UTC and 14:00 UTC as recurring
liquidity hand-offs.  The completed preceding session supplies explicit buy-
and sell-side liquidity.  Two independent scenarios are tested:

* session sweep failure: an opening-window raid of the prior session edge is
  rejected, followed by opposite order flow and an internal structure break;
* accepted opening drive: price establishes value outside the prior session,
  retests the edge from outside and resumes in the displacement direction.

The detector only emits ``TradePlan`` objects.  The shared portfolio probe keeps
one global position, applies one completed-bar execution delay, structural loss
sizing, 7 bps-per-side cost stress, maintenance-margin diagnostics and NAV
accounting.  The initial workflow evaluates only five frozen one-week segments;
a long run is justified only if the same rules transfer across those weeks.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from math import log
from pathlib import Path
import statistics
import sys
from typing import Any, Deque

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from core import AuctionBar, CandidateConfig, Response, Side, TradePlan  # noqa: E402
from data import DownloadRecord, load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from portfolio_probe import Pending, Variant, _aggregate_variant, simulate  # noqa: E402


NS_PER_MINUTE = 60_000_000_000
ONE_MILLISECOND_NS = 1_000_000
SESSION_STARTS = (0, 8 * 60, 14 * 60)
SESSION_NAMES = {0: "utc-roll", 8 * 60: "europe-settlement", 14 * 60: "us-open"}
ACTIVE_SESSION_NAMES = frozenset({"europe-settlement", "us-open"})
RISK_RATES = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)
RULES = (
    "all-session-sweep-mid",
    "active-open-sweep-mid",
    "active-open-sweep-opposite",
    "active-open-accepted-drive",
    "active-open-composite",
)


@dataclass(slots=True)
class SessionRange:
    key: str
    name: str
    start_ns: int
    high: float
    low: float
    open: float
    close: float
    bars: int = 1

    def update(self, bar: AuctionBar) -> None:
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        self.bars += 1

    @property
    def midpoint(self) -> float:
        return 0.5 * (self.high + self.low)

    @property
    def width(self) -> float:
        return self.high - self.low


@dataclass(slots=True)
class SweepAttempt:
    side: Side
    boundary: float
    extreme: float
    atr: float
    internal_break: float
    started_index: int
    expiry_index: int


@dataclass(slots=True)
class AcceptedDrive:
    side: Side
    boundary: float
    atr: float
    started_index: int
    expiry_index: int


@dataclass(frozen=True, slots=True)
class BarState:
    atr: float | None
    flow_z: float | None
    volume_z: float | None
    body_atr: float | None


@dataclass(frozen=True, slots=True)
class DetectorEvidence:
    event_type: str
    rule: str
    scenario_id: str
    session_key: str
    session_name: str
    signal_time_ns: int
    side: str
    boundary: float
    extreme: float
    atr: float
    flow_z: float
    volume_z: float
    body_atr: float
    target_style: str


class SessionLiquidityDetector:
    def __init__(self, config: CandidateConfig) -> None:
        self.config = config
        self.true_ranges: Deque[float] = deque(maxlen=config.atr_lookback)
        self.flows: Deque[float] = deque(maxlen=config.flow_lookback)
        self.log_volumes: Deque[float] = deque(maxlen=config.volume_lookback)
        self.recent_highs: Deque[float] = deque(maxlen=config.structure_lookback)
        self.recent_lows: Deque[float] = deque(maxlen=config.structure_lookback)
        self.previous_close: float | None = None
        self.index = 0
        self.builder: SessionRange | None = None
        self.anchor: SessionRange | None = None
        self.session_key: str | None = None
        self.session_name: str | None = None
        self.minute_in_session = 0
        self.sweep: SweepAttempt | None = None
        self.drive: AcceptedDrive | None = None
        self.drive_side: Side | None = None
        self.drive_count = 0
        self.drive_flow_sum = 0.0
        self.sweep_emitted = False
        self.drive_emitted = False
        self.schedules: dict[str, dict[int, list[Pending]]] = {
            rule: {} for rule in RULES
        }
        self.evidence: list[DetectorEvidence] = []
        self.rejections: dict[str, int] = {
            "insufficient_history": 0,
            "outside_trade_window": 0,
            "invalid_structural_target": 0,
            "expired_sweep": 0,
            "expired_drive": 0,
        }

    @staticmethod
    def _zscore(value: float, history: Deque[float]) -> float | None:
        if len(history) < 20:
            return None
        mean = statistics.fmean(history)
        variance = statistics.fmean((item - mean) ** 2 for item in history)
        return 0.0 if variance <= 0.0 else (value - mean) / variance**0.5

    @staticmethod
    def _session_identity(ts_event_ns: int) -> tuple[str, str, int, int]:
        # Binance close timestamps end at xx:xx:59.999.  Recover the minute-open
        # timestamp before assigning the session so the boundary is exact.
        open_ns = ts_event_ns + ONE_MILLISECOND_NS - NS_PER_MINUTE
        opened = datetime.fromtimestamp(open_ns / 1_000_000_000, tz=timezone.utc)
        minute = opened.hour * 60 + opened.minute
        start_minute = max(value for value in SESSION_STARTS if value <= minute)
        start_date = opened.date()
        start_dt = datetime(
            start_date.year,
            start_date.month,
            start_date.day,
            start_minute // 60,
            start_minute % 60,
            tzinfo=timezone.utc,
        )
        key = f"{start_dt.isoformat()}:{SESSION_NAMES[start_minute]}"
        return key, SESSION_NAMES[start_minute], minute - start_minute, int(start_dt.timestamp() * 1e9)

    def _bar_state(self, bar: AuctionBar) -> BarState:
        atr = statistics.fmean(self.true_ranges) if len(self.true_ranges) >= 20 else None
        flow_z = self._zscore(bar.aggressive_imbalance, self.flows)
        logged_volume = log(max(bar.quote_volume, 1e-12))
        volume_z = self._zscore(logged_volume, self.log_volumes)
        body_atr = (bar.close - bar.open) / atr if atr is not None and atr > 0.0 else None
        return BarState(atr=atr, flow_z=flow_z, volume_z=volume_z, body_atr=body_atr)

    def _start_session(
        self,
        *,
        key: str,
        name: str,
        start_ns: int,
        bar: AuctionBar,
    ) -> None:
        if self.builder is not None and self.builder.bars >= 300:
            self.anchor = self.builder
        self.builder = SessionRange(
            key=key,
            name=name,
            start_ns=start_ns,
            high=bar.high,
            low=bar.low,
            open=bar.open,
            close=bar.close,
        )
        self.session_key = key
        self.session_name = name
        self.sweep = None
        self.drive = None
        self.drive_side = None
        self.drive_count = 0
        self.drive_flow_sum = 0.0
        self.sweep_emitted = False
        self.drive_emitted = False

    def _minimum_stop(self, entry: float, stop: float, side: Side, atr: float) -> float:
        minimum = 0.45 * atr
        if side is Side.LONG:
            return min(stop, entry - minimum)
        return max(stop, entry + minimum)

    def _make_plan(
        self,
        *,
        rule: str,
        bar: AuctionBar,
        side: Side,
        stop: float,
        target: float,
        atr: float,
        sweep_extreme: float,
        target_style: str,
        max_hold_bars: int,
        reason_code: str,
        state: BarState,
    ) -> Pending | None:
        anchor = self.anchor
        if anchor is None or self.session_key is None or self.session_name is None:
            return None
        entry = bar.close
        stop = self._minimum_stop(entry, stop, side, atr)
        geometry = stop < entry < target if side is Side.LONG else target < entry < stop
        if not geometry:
            self.rejections["invalid_structural_target"] += 1
            return None
        risk = abs(entry - stop)
        reward = abs(target - entry)
        estimated_rr = reward / risk if risk > 0.0 else 0.0
        if estimated_rr < 1.35:
            self.rejections["invalid_structural_target"] += 1
            return None
        scenario_id = (
            f"session:{rule}:{self.session_key}:{side.value.lower()}:{bar.ts_event_ns}"
        )
        plan = TradePlan(
            scenario_id=scenario_id,
            side=side,
            response=(
                Response.ACCEPTANCE_FAILURE
                if "accepted-drive" in rule
                else Response.SWEEP_FAILURE
            ),
            signal_time_ns=bar.ts_event_ns,
            observed_time_ns=bar.ts_event_ns,
            expected_entry=entry,
            stop_price=stop,
            target_price=target,
            anchor_high=anchor.high,
            anchor_low=anchor.low,
            sweep_extreme=sweep_extreme,
            atr=atr,
            estimated_reward_risk=estimated_rr,
            max_hold_bars=max_hold_bars,
            reason_code=reason_code,
        )
        pending = Pending(symbol="BTCUSDT", horizon=480, plan=plan)
        self.schedules[rule].setdefault(bar.ts_event_ns, []).append(pending)
        self.evidence.append(
            DetectorEvidence(
                event_type=reason_code,
                rule=rule,
                scenario_id=scenario_id,
                session_key=self.session_key,
                session_name=self.session_name,
                signal_time_ns=bar.ts_event_ns,
                side=side.value,
                boundary=(anchor.low if side is Side.LONG else anchor.high),
                extreme=sweep_extreme,
                atr=atr,
                flow_z=float(state.flow_z or 0.0),
                volume_z=float(state.volume_z or 0.0),
                body_atr=float(state.body_atr or 0.0),
                target_style=target_style,
            ),
        )
        return pending

    def _emit_sweep_plans(
        self,
        *,
        bar: AuctionBar,
        state: BarState,
        attempt: SweepAttempt,
    ) -> None:
        anchor = self.anchor
        assert anchor is not None
        side = attempt.side
        buffer = 0.10 * attempt.atr
        stop = (
            attempt.extreme - buffer
            if side is Side.LONG
            else attempt.extreme + buffer
        )
        midpoint = anchor.midpoint
        opposite = anchor.high if side is Side.LONG else anchor.low
        self._make_plan(
            rule="all-session-sweep-mid",
            bar=bar,
            side=side,
            stop=stop,
            target=midpoint,
            atr=attempt.atr,
            sweep_extreme=attempt.extreme,
            target_style="prior-session-midpoint",
            max_hold_bars=120,
            reason_code="SESSION_SWEEP_FAILURE_CONFIRMED",
            state=state,
        )
        if self.session_name in ACTIVE_SESSION_NAMES:
            mid = self._make_plan(
                rule="active-open-sweep-mid",
                bar=bar,
                side=side,
                stop=stop,
                target=midpoint,
                atr=attempt.atr,
                sweep_extreme=attempt.extreme,
                target_style="prior-session-midpoint",
                max_hold_bars=120,
                reason_code="ACTIVE_OPEN_SWEEP_FAILURE_CONFIRMED",
                state=state,
            )
            self._make_plan(
                rule="active-open-sweep-opposite",
                bar=bar,
                side=side,
                stop=stop,
                target=opposite,
                atr=attempt.atr,
                sweep_extreme=attempt.extreme,
                target_style="prior-session-opposite-edge",
                max_hold_bars=240,
                reason_code="ACTIVE_OPEN_SWEEP_FAILURE_CONFIRMED",
                state=state,
            )
            if mid is not None:
                composite = Pending(
                    symbol=mid.symbol,
                    horizon=mid.horizon,
                    plan=TradePlan(
                        **{
                            **asdict(mid.plan),
                            "scenario_id": mid.plan.scenario_id.replace(
                                "active-open-sweep-mid",
                                "active-open-composite",
                            ),
                        },
                    ),
                )
                self.schedules["active-open-composite"].setdefault(
                    bar.ts_event_ns,
                    [],
                ).append(composite)
        self.sweep_emitted = True
        self.sweep = None

    def _emit_drive_plan(
        self,
        *,
        bar: AuctionBar,
        state: BarState,
        drive: AcceptedDrive,
    ) -> None:
        anchor = self.anchor
        assert anchor is not None
        side = drive.side
        stop = (
            min(bar.low, drive.boundary) - 0.10 * drive.atr
            if side is Side.LONG
            else max(bar.high, drive.boundary) + 0.10 * drive.atr
        )
        projection = 0.50 * anchor.width
        target = drive.boundary + side.sign * projection
        pending = self._make_plan(
            rule="active-open-accepted-drive",
            bar=bar,
            side=side,
            stop=stop,
            target=target,
            atr=drive.atr,
            sweep_extreme=(bar.low if side is Side.LONG else bar.high),
            target_style="half-prior-session-range-projection",
            max_hold_bars=180,
            reason_code="ACTIVE_OPEN_VALUE_ACCEPTED_RETEST",
            state=state,
        )
        if pending is not None:
            composite = Pending(
                symbol=pending.symbol,
                horizon=pending.horizon,
                plan=TradePlan(
                    **{
                        **asdict(pending.plan),
                        "scenario_id": pending.plan.scenario_id.replace(
                            "active-open-accepted-drive",
                            "active-open-composite",
                        ),
                    },
                ),
            )
            self.schedules["active-open-composite"].setdefault(
                bar.ts_event_ns,
                [],
            ).append(composite)
        self.drive_emitted = True
        self.drive = None

    def _observe_sweep(self, bar: AuctionBar, state: BarState) -> None:
        anchor = self.anchor
        if anchor is None or state.atr is None or state.flow_z is None or state.volume_z is None:
            self.rejections["insufficient_history"] += 1
            return
        atr = state.atr
        if self.sweep is None and not self.sweep_emitted and self.minute_in_session <= 120:
            low_excursion = (anchor.low - bar.low) / atr
            high_excursion = (bar.high - anchor.high) / atr
            long_attempt = (
                0.08 <= low_excursion <= 1.50
                and bar.close >= anchor.low + 0.04 * atr
                and (-state.flow_z >= 0.30 or state.volume_z >= 0.15)
            )
            short_attempt = (
                0.08 <= high_excursion <= 1.50
                and bar.close <= anchor.high - 0.04 * atr
                and (state.flow_z >= 0.30 or state.volume_z >= 0.15)
            )
            if long_attempt and self.recent_highs:
                self.sweep = SweepAttempt(
                    side=Side.LONG,
                    boundary=anchor.low,
                    extreme=bar.low,
                    atr=atr,
                    internal_break=max(self.recent_highs),
                    started_index=self.index,
                    expiry_index=self.index + 6,
                )
            elif short_attempt and self.recent_lows:
                self.sweep = SweepAttempt(
                    side=Side.SHORT,
                    boundary=anchor.high,
                    extreme=bar.high,
                    atr=atr,
                    internal_break=min(self.recent_lows),
                    started_index=self.index,
                    expiry_index=self.index + 6,
                )

        attempt = self.sweep
        if attempt is None:
            return
        if self.index > attempt.expiry_index:
            self.rejections["expired_sweep"] += 1
            self.sweep = None
            return
        if attempt.side is Side.LONG:
            attempt.extreme = min(attempt.extreme, bar.low)
            confirmed = (
                self.index > attempt.started_index
                and bar.close > attempt.internal_break
                and float(state.body_atr or 0.0) >= 0.25
                and state.flow_z >= 0.35
            )
        else:
            attempt.extreme = max(attempt.extreme, bar.high)
            confirmed = (
                self.index > attempt.started_index
                and bar.close < attempt.internal_break
                and float(state.body_atr or 0.0) <= -0.25
                and state.flow_z <= -0.35
            )
        if confirmed:
            self._emit_sweep_plans(bar=bar, state=state, attempt=attempt)

    def _observe_drive(self, bar: AuctionBar, state: BarState) -> None:
        anchor = self.anchor
        if (
            anchor is None
            or self.session_name not in ACTIVE_SESSION_NAMES
            or self.drive_emitted
            or state.atr is None
            or state.flow_z is None
        ):
            return
        atr = state.atr
        if self.drive is None and self.minute_in_session <= 90:
            side: Side | None = None
            if bar.close >= anchor.high + 0.08 * atr:
                side = Side.LONG
            elif bar.close <= anchor.low - 0.08 * atr:
                side = Side.SHORT
            if side is None:
                self.drive_side = None
                self.drive_count = 0
                self.drive_flow_sum = 0.0
            else:
                if side is self.drive_side:
                    self.drive_count += 1
                else:
                    self.drive_side = side
                    self.drive_count = 1
                    self.drive_flow_sum = 0.0
                self.drive_flow_sum += side.sign * state.flow_z
                boundary = anchor.high if side is Side.LONG else anchor.low
                displacement = side.sign * (bar.close - boundary) / atr
                if (
                    self.drive_count >= 3
                    and self.drive_flow_sum >= 0.75
                    and displacement >= 0.25
                ):
                    self.drive = AcceptedDrive(
                        side=side,
                        boundary=boundary,
                        atr=atr,
                        started_index=self.index,
                        expiry_index=self.index + 20,
                    )

        drive = self.drive
        if drive is None:
            return
        if self.index > drive.expiry_index:
            self.rejections["expired_drive"] += 1
            self.drive = None
            return
        if self.index <= drive.started_index:
            return
        if drive.side is Side.LONG:
            retest = (
                bar.low <= drive.boundary + 0.15 * drive.atr
                and bar.close >= drive.boundary + 0.04 * drive.atr
                and float(state.body_atr or 0.0) >= 0.08
                and state.flow_z >= 0.0
            )
        else:
            retest = (
                bar.high >= drive.boundary - 0.15 * drive.atr
                and bar.close <= drive.boundary - 0.04 * drive.atr
                and float(state.body_atr or 0.0) <= -0.08
                and state.flow_z <= 0.0
            )
        if retest:
            self._emit_drive_plan(bar=bar, state=state, drive=drive)

    def on_bar(self, bar: AuctionBar) -> None:
        self.index += 1
        state = self._bar_state(bar)
        key, name, minute_in_session, start_ns = self._session_identity(bar.ts_event_ns)
        if key != self.session_key:
            self._start_session(key=key, name=name, start_ns=start_ns, bar=bar)
        else:
            assert self.builder is not None
            self.builder.update(bar)
        self.minute_in_session = minute_in_session

        if self.anchor is not None:
            if minute_in_session <= 120:
                self._observe_sweep(bar, state)
            else:
                self.rejections["outside_trade_window"] += 1
            self._observe_drive(bar, state)

        previous = self.previous_close
        true_range = (
            bar.high - bar.low
            if previous is None
            else max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))
        )
        self.true_ranges.append(true_range)
        self.flows.append(bar.aggressive_imbalance)
        self.log_volumes.append(log(max(bar.quote_volume, 1e-12)))
        self.recent_highs.append(bar.high)
        self.recent_lows.append(bar.low)
        self.previous_close = bar.close


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
    downloads: list[dict[str, Any]] = []
    plan_counts: dict[str, dict[str, int]] = {}

    for label, start, end in week_segments(research):
        frame, records = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=720,
        )
        downloads.extend(asdict(record) for record in records)
        bars = to_auction_bars(frame)
        detector = SessionLiquidityDetector(candidate)
        for bar in bars:
            detector.on_bar(bar)
        pd.DataFrame(asdict(row) for row in detector.evidence).to_csv(
            output / f"{label}_detector_evidence.csv",
            index=False,
        )
        atomic_json(output / f"{label}_detector_rejections.json", detector.rejections)
        plan_counts[label] = {
            rule: sum(len(values) for values in detector.schedules[rule].values())
            for rule in RULES
        }

        for rule in RULES:
            schedule = {
                timestamp: tuple(rows)
                for timestamp, rows in detector.schedules[rule].items()
            }
            trades, metrics, daily = simulate(
                variant=Variant(rule, ("BTCUSDT",), (480,)),
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

    files = pd.DataFrame(downloads).drop_duplicates(["symbol", "month"])
    atomic_json(
        output / "download_manifest.json",
        {"provider": "Binance Vision", "records": files.to_dict(orient="records")},
    )
    summary = {
        "scenario": "session liquidity sweep failure and accepted opening drive",
        "sessions_utc": ["00:00", "08:00", "14:00"],
        "long_evaluation_run": False,
        "one_global_position": True,
        "one_bar_execution_delay": True,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "plan_counts": plan_counts,
        "aggregates": aggregates,
    }
    atomic_json(output / "session_liquidity_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-session-liquidity",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-session-liquidity",
    )
    parser.add_argument(
        "--risk-rates",
        default=",".join(str(value) for value in RISK_RATES),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
