#!/usr/bin/env python3
"""Fast causal portfolio probe across instruments, horizons, and risk rates.

This is a research diagnostic, not a replacement backtest engine.  It reuses the
exact candidate state machine, one completed-bar execution delay, structural
risk sizing, 7 bps-per-side stress cost, and a single global position.  Every
promising variant must still pass the real NautilusTrader order/accounting path.

The probe exists to answer structural questions efficiently:

* Is the failed-auction logic specific to a four-hour BTC range?
* Does the same causal response transfer across 1h/2h/4h/8h auction contexts?
* Does transfer to ETH/SOL/XRP add independent opportunities while preserving
  the one-position global constraint?
* What constant risk fraction maximizes observed geometric growth without
  relying on a model score or per-trade risk multiplier?
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from math import prod
from pathlib import Path
import sys
from typing import Any, Iterable

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from core import AuctionBar, AuctionStateMachine, CandidateConfig, Side, TradePlan  # noqa: E402
from data import DownloadRecord, load_interval, parse_utc_date, to_auction_bars  # noqa: E402


NS_PER_MINUTE = 60_000_000_000
MAINTENANCE_MARGIN_RATE = 1.0 / 125.0 / 2.0
DEFAULT_RISK_RATES = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08)


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    symbols: tuple[str, ...]
    horizons: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Pending:
    symbol: str
    horizon: int
    plan: TradePlan


@dataclass(frozen=True, slots=True)
class Viable:
    symbol: str
    horizon: int
    plan: TradePlan
    entry_time_ns: int
    entry: float
    stop: float
    target: float
    price_risk: float
    round_trip_cost_at_stop: float
    planned_loss_per_unit: float
    planned_gain_per_unit: float
    price_risk_fraction: float
    net_reward_risk: float


@dataclass(slots=True)
class Active:
    viable: Viable
    bars_held: int
    entry_nav: dict[float, float]
    minimum_mark_r: float = 0.0
    maximum_mark_r: float = 0.0


@dataclass(slots=True)
class RiskState:
    nav: float
    high_water: float
    max_drawdown: float = 0.0
    minimum_margin_ratio: float | None = None
    current_day: str | None = None
    current_day_nav: float | None = None
    daily_nav: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.daily_nav is None:
            self.daily_nav = []


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _utc_date(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).date().isoformat()


def _segments(config: dict[str, Any]) -> list[tuple[str, datetime, datetime]]:
    def week(label: str, value: str) -> tuple[str, datetime, datetime]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7)

    return [
        week("discovery", str(config["discovery_week"])),
        *[
            week(f"confirmation-{index + 1}", value)
            for index, value in enumerate(config["confirmation_weeks"])
        ],
    ]


def _default_variants() -> tuple[Variant, ...]:
    all_symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    all_horizons = (60, 120, 240, 480)
    return (
        Variant("btc-240", ("BTCUSDT",), (240,)),
        Variant("btc-multiscale", ("BTCUSDT",), all_horizons),
        Variant("multiasset-240", all_symbols, (240,)),
        Variant("multiasset-multiscale", all_symbols, all_horizons),
    )


def _load_segment(
    *,
    symbols: Iterable[str],
    start: datetime,
    end: datetime,
    cache_dir: Path,
    warmup_minutes: int,
) -> tuple[dict[str, list[AuctionBar]], list[DownloadRecord]]:
    result: dict[str, list[AuctionBar]] = {}
    records: list[DownloadRecord] = []
    for symbol in symbols:
        frame, downloaded = load_interval(
            symbol=symbol,
            start=start,
            end=end,
            cache_dir=cache_dir / symbol,
            warmup_minutes=warmup_minutes,
        )
        result[symbol] = to_auction_bars(frame)
        records.extend(downloaded)
    return result, records


def _viable(
    pending: Pending,
    bar: AuctionBar,
    *,
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> Viable | None:
    plan = pending.plan
    entry = bar.close
    if plan.side is Side.LONG and not plan.stop_price < entry < plan.target_price:
        return None
    if plan.side is Side.SHORT and not plan.target_price < entry < plan.stop_price:
        return None
    price_risk = abs(entry - plan.stop_price)
    round_trip_cost = entry * cost + plan.stop_price * cost
    planned_loss = price_risk + round_trip_cost
    planned_gain = abs(plan.target_price - entry) - entry * cost - plan.target_price * cost
    if planned_loss <= 0.0 or planned_gain <= 0.0:
        return None
    price_fraction = price_risk / planned_loss
    net_rr = planned_gain / planned_loss
    if price_fraction < minimum_price_risk_fraction or net_rr < minimum_net_reward_risk:
        return None
    return Viable(
        symbol=pending.symbol,
        horizon=pending.horizon,
        plan=plan,
        entry_time_ns=bar.ts_event_ns,
        entry=entry,
        stop=plan.stop_price,
        target=plan.target_price,
        price_risk=price_risk,
        round_trip_cost_at_stop=round_trip_cost,
        planned_loss_per_unit=planned_loss,
        planned_gain_per_unit=planned_gain,
        price_risk_fraction=price_fraction,
        net_reward_risk=net_rr,
    )


def _exit_r(viable: Viable, exit_price: float, cost: float) -> float:
    gross = (exit_price - viable.entry) * viable.plan.side.sign
    net = gross - viable.entry * cost - exit_price * cost
    return net / viable.planned_loss_per_unit


def _mark_r(viable: Viable, mark_price: float, cost: float) -> float:
    gross = (mark_price - viable.entry) * viable.plan.side.sign
    # Entry fee is already paid.  Exit cost is not charged until an actual exit.
    return (gross - viable.entry * cost) / viable.planned_loss_per_unit


def _mark_states(
    states: dict[float, RiskState],
    *,
    ts_ns: int,
    active: Active | None,
    mark_price: float | None,
) -> None:
    day = _utc_date(ts_ns)
    for risk, state in states.items():
        if active is None or mark_price is None:
            equity = state.nav
        else:
            mark_r = _mark_r(active.viable, mark_price, active_cost := 0.0)
            # ``active_cost`` is deliberately zero here because the reusable
            # helper below receives the actual stress cost through a cached
            # value attached by the caller.  The caller overwrites equity after
            # this function for active positions.  This branch is retained only
            # for type completeness and is never used.
            equity = active.entry_nav[risk] * (1.0 + risk * mark_r)
        if equity > state.high_water:
            state.high_water = equity
        if state.high_water > 0.0:
            state.max_drawdown = min(state.max_drawdown, equity / state.high_water - 1.0)
        if state.current_day is None:
            state.current_day = day
        elif day != state.current_day:
            assert state.current_day_nav is not None
            state.daily_nav.append({"date": state.current_day, "nav": state.current_day_nav})
            state.current_day = day
        state.current_day_nav = equity


def _record_equity(
    states: dict[float, RiskState],
    *,
    ts_ns: int,
    active: Active | None,
    mark_price: float | None,
    cost: float,
) -> None:
    day = _utc_date(ts_ns)
    for risk, state in states.items():
        if active is None or mark_price is None:
            equity = state.nav
        else:
            mark_r = _mark_r(active.viable, mark_price, cost)
            equity = active.entry_nav[risk] * (1.0 + risk * mark_r)
            notional = (
                active.entry_nav[risk]
                * risk
                / active.viable.planned_loss_per_unit
                * mark_price
            )
            maintenance = notional * MAINTENANCE_MARGIN_RATE
            if maintenance > 0.0:
                ratio = equity / maintenance
                if state.minimum_margin_ratio is None:
                    state.minimum_margin_ratio = ratio
                else:
                    state.minimum_margin_ratio = min(state.minimum_margin_ratio, ratio)
        if equity > state.high_water:
            state.high_water = equity
        if state.high_water > 0.0:
            state.max_drawdown = min(state.max_drawdown, equity / state.high_water - 1.0)
        if state.current_day is None:
            state.current_day = day
        elif day != state.current_day:
            assert state.current_day_nav is not None
            state.daily_nav.append({"date": state.current_day, "nav": state.current_day_nav})
            state.current_day = day
        state.current_day_nav = equity


def _close_trade(
    active: Active,
    *,
    exit_time_ns: int,
    exit_price: float,
    reason: str,
    cost: float,
    states: dict[float, RiskState],
    trades: list[dict[str, Any]],
) -> None:
    realized_r = _exit_r(active.viable, exit_price, cost)
    for risk, state in states.items():
        state.nav = active.entry_nav[risk] * (1.0 + risk * realized_r)
    trades.append(
        {
            **asdict(active.viable.plan),
            "side": active.viable.plan.side.value,
            "response": active.viable.plan.response.value,
            "symbol": active.viable.symbol,
            "horizon_minutes": active.viable.horizon,
            "entry_time_ns": active.viable.entry_time_ns,
            "entry": active.viable.entry,
            "stop": active.viable.stop,
            "target": active.viable.target,
            "price_risk_fraction": active.viable.price_risk_fraction,
            "net_reward_risk_at_entry": active.viable.net_reward_risk,
            "exit_time_ns": exit_time_ns,
            "exit_price": exit_price,
            "exit_reason": reason,
            "bars_held": active.bars_held,
            "realized_r": realized_r,
            "minimum_mark_r": active.minimum_mark_r,
            "maximum_mark_r": active.maximum_mark_r,
        },
    )


def simulate(
    *,
    variant: Variant,
    bars_by_symbol: dict[str, list[AuctionBar]],
    evaluation_start: datetime,
    evaluation_end: datetime,
    base_candidate: CandidateConfig,
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
    starting_nav: float,
    risk_rates: tuple[float, ...],
    allowed_scenario_ids: frozenset[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], dict[float, list[dict[str, Any]]]]:
    start_ns = int(pd.Timestamp(evaluation_start).value)
    end_ns = int(pd.Timestamp(evaluation_end).value)
    selected_bars = {symbol: bars_by_symbol[symbol] for symbol in variant.symbols}
    bar_maps = {
        symbol: {item.ts_event_ns: item for item in values}
        for symbol, values in selected_bars.items()
    }
    timestamps = sorted({ts for values in bar_maps.values() for ts in values})
    machines = {
        (symbol, horizon): AuctionStateMachine(
            replace(base_candidate, range_minutes=horizon),
            instrument_id=f"{symbol}-PERP.BINANCE:{horizon}m",
        )
        for symbol in variant.symbols
        for horizon in variant.horizons
    }
    states = {
        risk: RiskState(nav=starting_nav, high_water=starting_nav)
        for risk in risk_rates
    }
    pending: list[Pending] = []
    active: Active | None = None
    trades: list[dict[str, Any]] = []
    rejected = {
        "invalid_delayed_geometry": 0,
        "cost_dominated": 0,
        "insufficient_net_reward_risk": 0,
        "occupied": 0,
    }

    for ts_ns in timestamps:
        bars_now = {
            symbol: mapping[ts_ns]
            for symbol, mapping in bar_maps.items()
            if ts_ns in mapping
        }
        if not bars_now:
            continue

        occupied_at_start = active is not None
        if active is not None:
            mark = bars_now.get(active.viable.symbol)
            if mark is not None:
                active.bars_held += 1
                active.minimum_mark_r = min(
                    active.minimum_mark_r,
                    _mark_r(active.viable, mark.low if active.viable.plan.side is Side.LONG else mark.high, cost),
                )
                active.maximum_mark_r = max(
                    active.maximum_mark_r,
                    _mark_r(active.viable, mark.high if active.viable.plan.side is Side.LONG else mark.low, cost),
                )
                if active.viable.plan.side is Side.LONG:
                    stop_hit = mark.low <= active.viable.stop
                    target_hit = mark.high >= active.viable.target
                else:
                    stop_hit = mark.high >= active.viable.stop
                    target_hit = mark.low <= active.viable.target
                if stop_hit:
                    _close_trade(
                        active,
                        exit_time_ns=ts_ns,
                        exit_price=active.viable.stop,
                        reason="STOP",
                        cost=cost,
                        states=states,
                        trades=trades,
                    )
                    active = None
                elif target_hit:
                    _close_trade(
                        active,
                        exit_time_ns=ts_ns,
                        exit_price=active.viable.target,
                        reason="TARGET",
                        cost=cost,
                        states=states,
                        trades=trades,
                    )
                    active = None
                elif active.bars_held >= active.viable.plan.max_hold_bars:
                    _close_trade(
                        active,
                        exit_time_ns=ts_ns,
                        exit_price=mark.close,
                        reason="TIME",
                        cost=cost,
                        states=states,
                        trades=trades,
                    )
                    active = None

        if not occupied_at_start and active is None and pending and start_ns <= ts_ns < end_ns:
            viable_rows: list[Viable] = []
            for item in pending:
                current_bar = bars_now.get(item.symbol)
                if current_bar is None:
                    rejected["invalid_delayed_geometry"] += 1
                    continue
                plan = item.plan
                entry = current_bar.close
                geometry_ok = (
                    plan.stop_price < entry < plan.target_price
                    if plan.side is Side.LONG
                    else plan.target_price < entry < plan.stop_price
                )
                if not geometry_ok:
                    rejected["invalid_delayed_geometry"] += 1
                    continue
                price_risk = abs(entry - plan.stop_price)
                total_loss = price_risk + entry * cost + plan.stop_price * cost
                price_fraction = price_risk / total_loss if total_loss > 0.0 else 0.0
                if price_fraction < minimum_price_risk_fraction:
                    rejected["cost_dominated"] += 1
                    continue
                gain = abs(plan.target_price - entry) - entry * cost - plan.target_price * cost
                net_rr = gain / total_loss if total_loss > 0.0 else -1.0
                if gain <= 0.0 or net_rr < minimum_net_reward_risk:
                    rejected["insufficient_net_reward_risk"] += 1
                    continue
                row = _viable(
                    item,
                    current_bar,
                    cost=cost,
                    minimum_price_risk_fraction=minimum_price_risk_fraction,
                    minimum_net_reward_risk=minimum_net_reward_risk,
                )
                if row is not None:
                    viable_rows.append(row)
            if viable_rows:
                chosen = sorted(
                    viable_rows,
                    key=lambda item: (
                        -item.net_reward_risk,
                        -item.horizon,
                        item.symbol,
                        item.plan.scenario_id,
                    ),
                )[0]
                active = Active(
                    viable=chosen,
                    bars_held=0,
                    entry_nav={risk: state.nav for risk, state in states.items()},
                )
                rejected["occupied"] += max(len(viable_rows) - 1, 0)
        elif pending:
            rejected["occupied"] += len(pending)
        pending = []

        if start_ns <= ts_ns < end_ns and active is None:
            generated: list[Pending] = []
        else:
            generated = []
        for symbol, current_bar in bars_now.items():
            if symbol not in variant.symbols:
                continue
            for horizon in variant.horizons:
                plan = machines[(symbol, horizon)].on_bar(current_bar)
                if (
                    plan is not None
                    and start_ns <= ts_ns < end_ns
                    and active is None
                    and (
                        allowed_scenario_ids is None
                        or plan.scenario_id in allowed_scenario_ids
                    )
                ):
                    generated.append(Pending(symbol=symbol, horizon=horizon, plan=plan))
        pending = generated

        active_mark = bars_now.get(active.viable.symbol).close if active is not None and active.viable.symbol in bars_now else None
        if ts_ns >= start_ns:
            _record_equity(
                states,
                ts_ns=ts_ns,
                active=active,
                mark_price=active_mark,
                cost=cost,
            )

    if active is not None:
        final_bar = bars_by_symbol[active.viable.symbol][-1]
        _close_trade(
            active,
            exit_time_ns=final_bar.ts_event_ns,
            exit_price=final_bar.close,
            reason="EVALUATION_END",
            cost=cost,
            states=states,
            trades=trades,
        )
        active = None

    days = max((evaluation_end - evaluation_start).total_seconds() / 86_400.0, 1.0)
    risk_metrics: dict[str, Any] = {}
    daily_by_risk: dict[float, list[dict[str, Any]]] = {}
    for risk, state in states.items():
        if state.current_day is not None and state.current_day_nav is not None:
            if not state.daily_nav or state.daily_nav[-1]["date"] != state.current_day:
                state.daily_nav.append({"date": state.current_day, "nav": state.current_day_nav})
        daily_by_risk[risk] = list(state.daily_nav)
        total_return = state.nav / starting_nav - 1.0
        geo = (state.nav / starting_nav) ** (1.0 / days) - 1.0 if state.nav > 0.0 else -1.0
        risk_metrics[f"{risk:.4f}"] = {
            "risk_fraction": risk,
            "start_nav": starting_nav,
            "final_nav": state.nav,
            "total_return": total_return,
            "geometric_mean_daily_return": geo,
            "max_drawdown": state.max_drawdown,
            "minimum_equity_to_maintenance_margin": state.minimum_margin_ratio,
            "target_met": geo >= 0.01,
        }

    trade_frame = pd.DataFrame(trades)
    realized = pd.to_numeric(trade_frame.get("realized_r", pd.Series(dtype=float)), errors="coerce").dropna()
    summary = {
        "variant": variant.name,
        "symbols": list(variant.symbols),
        "horizons": list(variant.horizons),
        "evaluation_start_utc": evaluation_start.isoformat(),
        "evaluation_end_utc": evaluation_end.isoformat(),
        "calendar_days": days,
        "trades": int(len(trade_frame.index)),
        "trades_per_day": len(trade_frame.index) / days,
        "win_rate": float((realized > 0.0).mean()) if len(realized) else None,
        "mean_realized_r": float(realized.mean()) if len(realized) else None,
        "sum_realized_r": float(realized.sum()) if len(realized) else 0.0,
        "profit_factor_r": (
            float(realized[realized > 0.0].sum()) / abs(float(realized[realized < 0.0].sum()))
            if len(realized) and float(realized[realized < 0.0].sum()) < 0.0
            else None
        ),
        "exit_counts": trade_frame.get("exit_reason", pd.Series(dtype=str)).value_counts().to_dict(),
        "symbol_counts": trade_frame.get("symbol", pd.Series(dtype=str)).value_counts().to_dict(),
        "horizon_counts": {
            str(key): int(value)
            for key, value in trade_frame.get("horizon_minutes", pd.Series(dtype=int)).value_counts().to_dict().items()
        },
        "rejections": rejected,
        "risk_metrics": risk_metrics,
    }
    return trade_frame, summary, daily_by_risk


def _aggregate_variant(rows: list[dict[str, Any]], risk_rates: tuple[float, ...]) -> dict[str, Any]:
    total_days = sum(float(row["calendar_days"]) for row in rows)
    result: dict[str, Any] = {
        "segments": rows,
        "total_calendar_days": total_days,
        "total_trades": sum(int(row["trades"]) for row in rows),
        "risk_metrics": {},
    }
    for risk in risk_rates:
        key = f"{risk:.4f}"
        growth = prod(1.0 + float(row["risk_metrics"][key]["total_return"]) for row in rows)
        geo = growth ** (1.0 / total_days) - 1.0 if growth > 0.0 else -1.0
        worst_dd = min(float(row["risk_metrics"][key]["max_drawdown"]) for row in rows)
        margins = [
            float(row["risk_metrics"][key]["minimum_equity_to_maintenance_margin"])
            for row in rows
            if row["risk_metrics"][key]["minimum_equity_to_maintenance_margin"] is not None
        ]
        result["risk_metrics"][key] = {
            "risk_fraction": risk,
            "pooled_growth_factor": growth,
            "pooled_geometric_mean_daily_return": geo,
            "worst_segment_max_drawdown": worst_dd,
            "minimum_equity_to_maintenance_margin": min(margins, default=None),
            "target_met": geo >= 0.01,
            "drawdown_below_twenty_percent": worst_dd > -0.20,
        }
    return result


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    base_candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    risk_rates = tuple(float(item) for item in args.risk_rates.split(","))
    variants = _default_variants()
    all_symbols = sorted({symbol for variant in variants for symbol in variant.symbols})
    max_horizon = max(horizon for variant in variants for horizon in variant.horizons)
    warmup = max(int(research.get("warmup_minutes", 420)), max_horizon + 180)
    cost = float(execution["all_in_cost_bps_per_side"]) / 10_000.0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    aggregate_rows: dict[str, list[dict[str, Any]]] = {variant.name: [] for variant in variants}
    download_rows: list[dict[str, Any]] = []
    for label, start, end in _segments(research):
        bars_by_symbol, records = _load_segment(
            symbols=all_symbols,
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=warmup,
        )
        download_rows.extend(asdict(record) for record in records)
        for variant in variants:
            trades, summary, daily = simulate(
                variant=variant,
                bars_by_symbol=bars_by_symbol,
                evaluation_start=start,
                evaluation_end=end,
                base_candidate=base_candidate,
                cost=cost,
                minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
                minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
                starting_nav=float(execution["starting_nav"]),
                risk_rates=risk_rates,
            )
            destination = output / variant.name / label
            destination.mkdir(parents=True, exist_ok=True)
            trades.to_csv(destination / "trades.csv", index=False)
            _atomic_json(destination / "metrics.json", summary)
            for risk, rows in daily.items():
                pd.DataFrame(rows).to_csv(destination / f"daily_nav_{risk:.4f}.csv", index=False)
            aggregate_rows[variant.name].append(summary)

    all_aggregates: dict[str, Any] = {}
    for variant in variants:
        aggregate = _aggregate_variant(aggregate_rows[variant.name], risk_rates)
        _atomic_json(output / variant.name / "aggregate_metrics.json", aggregate)
        all_aggregates[variant.name] = aggregate
    _atomic_json(
        output / "download_manifest.json",
        {"provider": "Binance Vision", "records": download_rows},
    )
    _atomic_json(output / "portfolio_probe_summary.json", all_aggregates)
    print(json.dumps(all_aggregates, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-portfolio")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-portfolio-probe")
    parser.add_argument(
        "--risk-rates",
        default=",".join(str(value) for value in DEFAULT_RISK_RATES),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
