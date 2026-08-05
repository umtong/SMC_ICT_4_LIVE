#!/usr/bin/env python3
"""First-week stop-market entry test for the frozen LTF MSS scenario.

The signal, repeated-pool sweep, frozen one-minute MSS level, structural stop,
opposing-pool target, response window and 3% risk remain unchanged. Instead of
entering at the next minute close, one stop-market entry is armed beyond the
completed MSS bar extreme for three minutes. This is the final controlled
repair for the resting-pool family: it must improve executable opportunity
without weakening the scenario or it is rejected.

The stop order is causal and realistic:

* only one pending new-entry order or position may exist;
* the order starts on the minute after the signal and expires after three bars;
* a gap fills at the worse of trigger or bar open;
* source-stop or target contact before entry cancels the order;
* same-bar stop/target ambiguity after a fill is resolved stop-first;
* 7 bps per side includes fees and execution stress;
* quantity risks exactly 3% of current NAV at the structural stop.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from math import prod
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

from core import AuctionBar, CandidateConfig, Side  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
import ltf_mss_resting_pool_probe as base  # noqa: E402
from portfolio_probe import Pending, Variant, simulate  # noqa: E402
from resting_liquidity_pool_probe import aggregate_five_minute  # noqa: E402


RISK_RATE = 0.03
RESPONSE_WINDOW_MINUTES = 10
ENTRY_EXPIRY_BARS = 3
TICK_SIZE = 0.1


@dataclass(frozen=True, slots=True)
class StopOrder:
    pending: Pending
    trigger: float
    placed_time_ns: int
    expiry_time_ns: int


@dataclass(slots=True)
class Active:
    order: StopOrder
    entry_time_ns: int
    entry: float
    planned_loss_per_unit: float
    planned_gain_per_unit: float
    price_risk_fraction: float
    net_reward_risk: float
    entry_nav: float
    quantity: float
    bars_held: int = 0
    minimum_mark_r: float = 0.0
    maximum_mark_r: float = 0.0


@dataclass(frozen=True, slots=True)
class Trade:
    scenario_id: str
    side: str
    signal_time_ns: int
    order_time_ns: int
    trigger: float
    entry_time_ns: int
    entry: float
    stop: float
    target: float
    confirmation_hold_price: float | None
    price_risk_fraction: float
    net_reward_risk_at_entry: float
    exit_time_ns: int
    exit_price: float
    exit_reason: str
    bars_held: int
    realized_r: float
    minimum_mark_r: float
    maximum_mark_r: float
    entry_nav: float
    exit_nav: float


def utc_date(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).date().isoformat()


def net_per_unit(*, side: Side, entry: float, exit_price: float, cost: float) -> float:
    return side.sign * (exit_price - entry) - entry * cost - exit_price * cost


def mark_r(active: Active, price: float, cost: float) -> float:
    return net_per_unit(
        side=active.order.pending.plan.side,
        entry=active.entry,
        exit_price=price,
        cost=cost,
    ) / active.planned_loss_per_unit


def close_active(
    active: Active,
    *,
    exit_time_ns: int,
    exit_price: float,
    reason: str,
    cost: float,
) -> Trade:
    plan = active.order.pending.plan
    realized_r = net_per_unit(
        side=plan.side,
        entry=active.entry,
        exit_price=exit_price,
        cost=cost,
    ) / active.planned_loss_per_unit
    exit_nav = active.entry_nav * (1.0 + RISK_RATE * realized_r)
    return Trade(
        scenario_id=plan.scenario_id,
        side=plan.side.value,
        signal_time_ns=plan.signal_time_ns,
        order_time_ns=active.order.placed_time_ns,
        trigger=active.order.trigger,
        entry_time_ns=active.entry_time_ns,
        entry=active.entry,
        stop=plan.stop_price,
        target=plan.target_price,
        confirmation_hold_price=active.order.pending.confirmation_hold_price,
        price_risk_fraction=active.price_risk_fraction,
        net_reward_risk_at_entry=active.net_reward_risk,
        exit_time_ns=exit_time_ns,
        exit_price=exit_price,
        exit_reason=reason,
        bars_held=active.bars_held,
        realized_r=realized_r,
        minimum_mark_r=active.minimum_mark_r,
        maximum_mark_r=active.maximum_mark_r,
        entry_nav=active.entry_nav,
        exit_nav=exit_nav,
    )


def simulate_stop_entries(
    *,
    bars: list[AuctionBar],
    schedule: dict[int, tuple[Pending, ...]],
    start: datetime,
    end: datetime,
    cost: float,
    starting_nav: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    start_ns = int(pd.Timestamp(start).as_unit("ns").value)
    end_ns = int(pd.Timestamp(end).as_unit("ns").value)
    bar_by_time = {bar.ts_event_ns: bar for bar in bars}
    timestamps = sorted(bar_by_time)
    pending_order: StopOrder | None = None
    active: Active | None = None
    trades: list[Trade] = []
    nav = starting_nav
    high_water = starting_nav
    max_drawdown = 0.0
    current_day: str | None = None
    current_day_nav = starting_nav
    daily_rows: list[dict[str, Any]] = []
    counters = {
        "signals": 0,
        "orders_armed": 0,
        "occupied_signals": 0,
        "expired_orders": 0,
        "stop_touched_before_entry": 0,
        "target_touched_before_entry": 0,
        "triggered_orders": 0,
        "cost_dominated": 0,
        "insufficient_net_reward_risk": 0,
        "invalid_fill_geometry": 0,
        "same_bar_stop_first": 0,
    }

    for ts_ns in timestamps:
        bar = bar_by_time[ts_ns]
        if ts_ns < start_ns:
            continue
        if ts_ns >= end_ns:
            break

        if active is not None:
            active.bars_held += 1
            adverse = bar.low if active.order.pending.plan.side is Side.LONG else bar.high
            favorable = bar.high if active.order.pending.plan.side is Side.LONG else bar.low
            active.minimum_mark_r = min(active.minimum_mark_r, mark_r(active, adverse, cost))
            active.maximum_mark_r = max(active.maximum_mark_r, mark_r(active, favorable, cost))
            plan = active.order.pending.plan
            if plan.side is Side.LONG:
                stop_hit = bar.low <= plan.stop_price
                target_hit = bar.high >= plan.target_price
            else:
                stop_hit = bar.high >= plan.stop_price
                target_hit = bar.low <= plan.target_price
            closed: Trade | None = None
            if stop_hit:
                closed = close_active(
                    active,
                    exit_time_ns=ts_ns,
                    exit_price=plan.stop_price,
                    reason="STOP",
                    cost=cost,
                )
            elif target_hit:
                closed = close_active(
                    active,
                    exit_time_ns=ts_ns,
                    exit_price=plan.target_price,
                    reason="TARGET",
                    cost=cost,
                )
            elif active.bars_held >= plan.max_hold_bars:
                closed = close_active(
                    active,
                    exit_time_ns=ts_ns,
                    exit_price=bar.close,
                    reason="TIME",
                    cost=cost,
                )
            if closed is not None:
                nav = closed.exit_nav
                trades.append(closed)
                active = None

        if active is None and pending_order is not None:
            order = pending_order
            plan = order.pending.plan
            if ts_ns > order.expiry_time_ns:
                counters["expired_orders"] += 1
                pending_order = None
            else:
                stop_before = (
                    bar.low <= plan.stop_price
                    if plan.side is Side.LONG
                    else bar.high >= plan.stop_price
                )
                target_before = (
                    bar.high >= plan.target_price
                    if plan.side is Side.LONG
                    else bar.low <= plan.target_price
                )
                triggered = (
                    bar.high >= order.trigger
                    if plan.side is Side.LONG
                    else bar.low <= order.trigger
                )
                if stop_before and not triggered:
                    counters["stop_touched_before_entry"] += 1
                    pending_order = None
                elif target_before and not triggered:
                    counters["target_touched_before_entry"] += 1
                    pending_order = None
                elif triggered:
                    counters["triggered_orders"] += 1
                    entry = (
                        max(order.trigger, bar.open)
                        if plan.side is Side.LONG
                        else min(order.trigger, bar.open)
                    )
                    geometry = (
                        plan.stop_price < entry < plan.target_price
                        if plan.side is Side.LONG
                        else plan.target_price < entry < plan.stop_price
                    )
                    if not geometry:
                        counters["invalid_fill_geometry"] += 1
                        pending_order = None
                    else:
                        price_risk = abs(entry - plan.stop_price)
                        planned_loss = price_risk + entry * cost + plan.stop_price * cost
                        planned_gain = (
                            abs(plan.target_price - entry)
                            - entry * cost
                            - plan.target_price * cost
                        )
                        price_fraction = price_risk / planned_loss if planned_loss > 0.0 else 0.0
                        net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
                        if price_fraction < minimum_price_risk_fraction:
                            counters["cost_dominated"] += 1
                            pending_order = None
                        elif planned_gain <= 0.0 or net_rr < minimum_net_reward_risk:
                            counters["insufficient_net_reward_risk"] += 1
                            pending_order = None
                        else:
                            quantity = nav * RISK_RATE / planned_loss
                            active = Active(
                                order=order,
                                entry_time_ns=ts_ns,
                                entry=entry,
                                planned_loss_per_unit=planned_loss,
                                planned_gain_per_unit=planned_gain,
                                price_risk_fraction=price_fraction,
                                net_reward_risk=net_rr,
                                entry_nav=nav,
                                quantity=quantity,
                            )
                            pending_order = None
                            stop_hit = (
                                bar.low <= plan.stop_price
                                if plan.side is Side.LONG
                                else bar.high >= plan.stop_price
                            )
                            target_hit = (
                                bar.high >= plan.target_price
                                if plan.side is Side.LONG
                                else bar.low <= plan.target_price
                            )
                            if stop_hit or target_hit:
                                counters["same_bar_stop_first"] += 1
                                exit_price = plan.stop_price if stop_hit else plan.target_price
                                reason = "STOP" if stop_hit else "TARGET"
                                closed = close_active(
                                    active,
                                    exit_time_ns=ts_ns,
                                    exit_price=exit_price,
                                    reason=reason,
                                    cost=cost,
                                )
                                nav = closed.exit_nav
                                trades.append(closed)
                                active = None

        signals = schedule.get(ts_ns, ())
        counters["signals"] += len(signals)
        for item in signals:
            if active is not None or pending_order is not None:
                counters["occupied_signals"] += 1
                continue
            signal_bar = bar
            trigger = (
                signal_bar.high + TICK_SIZE
                if item.plan.side is Side.LONG
                else signal_bar.low - TICK_SIZE
            )
            pending_order = StopOrder(
                pending=item,
                trigger=trigger,
                placed_time_ns=ts_ns,
                expiry_time_ns=ts_ns + ENTRY_EXPIRY_BARS * base.ONE_MINUTE_NS,
            )
            counters["orders_armed"] += 1

        mark_nav = nav
        if active is not None:
            plan = active.order.pending.plan
            unrealized = active.quantity * net_per_unit(
                side=plan.side,
                entry=active.entry,
                exit_price=bar.close,
                cost=cost,
            )
            mark_nav = active.entry_nav + unrealized
        high_water = max(high_water, mark_nav)
        if high_water > 0.0:
            max_drawdown = min(max_drawdown, mark_nav / high_water - 1.0)
        day = utc_date(ts_ns)
        if current_day is None:
            current_day = day
        elif day != current_day:
            daily_rows.append({"date": current_day, "nav": current_day_nav})
            current_day = day
        current_day_nav = mark_nav

    if active is not None:
        final_bar = next(
            (bar for bar in reversed(bars) if bar.ts_event_ns < end_ns),
            bars[-1],
        )
        closed = close_active(
            active,
            exit_time_ns=final_bar.ts_event_ns,
            exit_price=final_bar.close,
            reason="EVALUATION_END",
            cost=cost,
        )
        nav = closed.exit_nav
        trades.append(closed)
    if current_day is not None:
        daily_rows.append({"date": current_day, "nav": nav})

    frame = pd.DataFrame(asdict(row) for row in trades)
    values = pd.to_numeric(frame.get("realized_r", pd.Series(dtype=float)), errors="coerce").dropna()
    gains = float(values[values > 0.0].sum())
    losses = abs(float(values[values < 0.0].sum()))
    days = max((end - start).total_seconds() / 86_400.0, 1.0)
    metrics = {
        "variant": "ltf-mss-stop-entry",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "calendar_days": days,
        "entry_expiry_bars": ENTRY_EXPIRY_BARS,
        "trigger_tick_size": TICK_SIZE,
        "trades": int(len(values)),
        "trades_per_day": len(values) / days,
        "win_rate": float((values > 0.0).mean()) if len(values) else None,
        "sum_realized_r": float(values.sum()) if len(values) else 0.0,
        "mean_realized_r": float(values.mean()) if len(values) else None,
        "profit_factor_r": gains / losses if losses > 0.0 else None,
        "exit_counts": frame.get("exit_reason", pd.Series(dtype=str)).value_counts().to_dict(),
        "start_nav": starting_nav,
        "final_nav": nav,
        "total_return": nav / starting_nav - 1.0,
        "geometric_mean_daily_return": (nav / starting_nav) ** (1.0 / days) - 1.0,
        "max_drawdown": max_drawdown,
        "target_met": (nav / starting_nav) ** (1.0 / days) - 1.0 >= 0.01,
        "counters": counters,
    }
    return frame, metrics, pd.DataFrame(daily_rows)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    base.MSS_EXPIRY_MINUTES = RESPONSE_WINDOW_MINUTES
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    start = parse_utc_date(str(research["discovery_week"]))
    end = start + timedelta(days=7)
    start_ns = int(pd.Timestamp(start).as_unit("ns").value)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0

    market, records = load_interval(
        symbol="BTCUSDT",
        start=start,
        end=end,
        cache_dir=args.cache,
        warmup_minutes=3 * 24 * 60,
    )
    minute_bars = to_auction_bars(market)
    five_minute_map = {
        bar.ts_event_ns: bar for bar in aggregate_five_minute(market)
    }
    detector = base.LtfMssDetector(candidate, evaluation_start_ns=start_ns)
    for minute_bar in minute_bars:
        five_minute_bar = five_minute_map.get(minute_bar.ts_event_ns)
        if five_minute_bar is not None:
            detector.on_five_minute(five_minute_bar)
        detector.on_one_minute(minute_bar)
    schedule = {
        timestamp: tuple(rows)
        for timestamp, rows in detector.schedules["ltf-mss-market"].items()
    }

    baseline_trades, baseline_metrics, baseline_daily = simulate(
        variant=Variant("ltf-mss-market-hold", ("BTCUSDT",), (60,)),
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
    stop_trades, stop_metrics, stop_daily = simulate_stop_entries(
        bars=minute_bars,
        schedule=schedule,
        start=start,
        end=end,
        cost=cost,
        starting_nav=float(execution["starting_nav"]),
        minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
        minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
    )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    baseline_trades.to_csv(output / "baseline_trades.csv", index=False)
    stop_trades.to_csv(output / "stop_entry_trades.csv", index=False)
    pd.DataFrame(baseline_daily[RISK_RATE]).to_csv(output / "baseline_daily_nav.csv", index=False)
    stop_daily.to_csv(output / "stop_entry_daily_nav.csv", index=False)
    payload = {
        "scenario": "frozen LTF MSS next-close versus stop-market entry",
        "evaluation_start_utc": start.isoformat(),
        "evaluation_end_utc": end.isoformat(),
        "response_window_minutes": RESPONSE_WINDOW_MINUTES,
        "risk_fraction": RISK_RATE,
        "all_in_cost_bps_per_side": float(execution["all_in_cost_bps_per_side"]),
        "baseline": baseline_metrics,
        "stop_entry": stop_metrics,
        "detector_stage_counts": dict(detector.stage_counts),
        "detector_rule_counts": dict(detector.rule_counts["ltf-mss-market"]),
        "downloads": [asdict(record) for record in records],
        "long_evaluation_run": False,
    }
    atomic_json(output / "ltf_mss_stop_entry_summary.json", payload)
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
        default=ROOT / "artifacts" / "candidate-01-ltf-mss-stop-entry",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
