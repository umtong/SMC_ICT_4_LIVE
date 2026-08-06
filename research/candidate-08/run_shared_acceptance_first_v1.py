"""Run the fixed first multi-asset week in one shared NautilusTrader account."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import timedelta
from decimal import Decimal
from inspect import signature
import json
from math import exp, log
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np

from nautilus_trader.analysis.reporter import ReportProvider
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from aggtrade_acceptance_causal_v1 import build_acceptance_signals
from aggtrade_acceptance_shared_strategy_v1 import (
    SharedAcceptanceStrategy,
    SharedAcceptanceStrategyConfig,
)
from aggtrade_orderflow_probe import load_ten_second_aggtrades
from data import load_official_binance_bars
from range_fvg_logic import (
    RangeFVGConfig,
    _bar_from_row,
    _build_level_snapshots,
    aggregate_five_minute_bars,
)
from run import (
    _create_engine,
    _equity_drawdown,
    _json_safe,
    _ns,
    _parse_utc,
    _position_metrics,
)
from smc_ict_4.manifest import create_run_manifest, write_json_atomic


def _source_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": str(value)}


def _instrument(asset: Mapping[str, Any], fee: Decimal) -> CryptoPerpetual:
    symbol = str(asset["symbol"])
    base = Currency.from_str(symbol.removesuffix("USDT"))
    usdt = Currency.from_str("USDT")
    tick = Decimal(str(asset["price_increment"]))
    step = Decimal(str(asset["size_increment"]))
    precision = int(asset["price_precision"])
    max_price_text = "1000000000" if precision == 0 else "1000000000." + "0" * precision
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(str(asset["instrument_id"])),
        raw_symbol=Symbol(symbol),
        base_currency=base,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=precision,
        price_increment=Price.from_str(format(tick, "f")),
        size_precision=int(asset["size_precision"]),
        size_increment=Quantity.from_str(format(step, "f")),
        max_quantity=None,
        min_quantity=Quantity.from_str(format(step, "f")),
        max_notional=None,
        min_notional=Money(10.0, usdt),
        max_price=Price.from_str(max_price_text),
        min_price=Price.from_str(format(tick, "f")),
        margin_init=Decimal("1.0"),
        margin_maint=Decimal("0.5"),
        maker_fee=fee,
        taker_fee=fee,
        ts_event=0,
        ts_init=0,
        info={"source": "candidate-08 shared acceptance", "all_in_fee": True},
    )


def _load_agg(symbol: str, start: Any, end: Any, cache: Path) -> Any:
    candidates = {
        "symbol": symbol,
        "start": start,
        "end": end,
        "load_start": start,
        "load_end": end,
        "cache_dir": cache,
        "data_cache": cache,
        "bucket_seconds": 10,
    }
    parameters = signature(load_ten_second_aggtrades).parameters
    kwargs = {name: candidates[name] for name in parameters if name in candidates}
    missing = [
        name
        for name, parameter in parameters.items()
        if parameter.default is parameter.empty
        and parameter.kind.name not in {"VAR_POSITIONAL", "VAR_KEYWORD"}
        and name not in kwargs
    ]
    if missing:
        raise RuntimeError(f"unsupported aggTrades loader signature: {missing}")
    return load_ten_second_aggtrades(**kwargs)


def _agg_parts(loaded: Any) -> tuple[Any, Any, Any]:
    if hasattr(loaded, "frame"):
        return loaded.frame, getattr(loaded, "source_files", ()), getattr(loaded, "quality", {})
    if isinstance(loaded, tuple) and len(loaded) == 3:
        return loaded[0], loaded[1], loaded[2]
    raise TypeError(f"unsupported aggTrades result: {type(loaded)!r}")


def _context(frame: Any, config: RangeFVGConfig) -> tuple[np.ndarray, tuple[Any, ...], tuple[Any, ...]]:
    five = aggregate_five_minute_bars(frame, config)
    bars = tuple(
        bar
        for index, (timestamp, row) in enumerate(five.iterrows())
        if (bar := _bar_from_row(index, timestamp, row)) is not None
    )
    snapshots = _build_level_snapshots(bars, config)
    return np.asarray([bar.ts_event_ns for bar in bars], dtype=np.int64), bars, snapshots


def _bars(frame: Any, bar_type: BarType, instrument: CryptoPerpetual) -> list[Bar]:
    values = frame[["open", "high", "low", "close", "volume"]].to_numpy(
        dtype="float64", copy=True,
    )
    timestamps = frame.index.as_unit("ns").asi8.copy()
    return [
        Bar(
            bar_type=bar_type,
            open=Price(float(row[0]), instrument.price_precision),
            high=Price(float(row[1]), instrument.price_precision),
            low=Price(float(row[2]), instrument.price_precision),
            close=Price(float(row[3]), instrument.price_precision),
            volume=Quantity(float(row[4]), instrument.size_precision),
            ts_event=int(timestamp),
            ts_init=int(timestamp),
        )
        for row, timestamp in zip(values, timestamps, strict=True)
    ]


def _engine_config(config: Mapping[str, Any]) -> dict[str, Any]:
    mapped = json.loads(json.dumps(config))
    mapped.setdefault("random_seed", 8208)
    mapped.setdefault("cost_assumptions", {})
    mapped["cost_assumptions"].setdefault("limit_fill_probability", 1.0)
    mapped["cost_assumptions"].setdefault("one_tick_slippage_probability", 1.0)
    mapped["cost_assumptions"].setdefault(
        "latency_ms", {"base": 0, "insert": 0, "update": 0, "cancel": 0},
    )
    return mapped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-cache", type=Path, required=True)
    args = parser.parse_args()

    config_path = args.config.resolve()
    output = args.output.resolve()
    cache = args.data_cache.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    window = dict(config["suites"]["first"][0])
    start = _parse_utc(str(window["start"]))
    end = _parse_utc(str(window["end"]))
    post_end = end + timedelta(hours=4, minutes=10)
    context_start = start - timedelta(days=10)
    fee = Decimal(str(config["effective_fee_rate_per_fill"]))
    pattern = RangeFVGConfig.from_mapping(dict(config["pattern"]))

    instruments: list[CryptoPerpetual] = []
    bar_types: list[BarType] = []
    all_data: list[Bar] = []
    signals: dict[int, list[Any]] = {}
    diagnostics: dict[str, Any] = {}
    manifest: dict[str, Any] = {
        "candidate": config["candidate"],
        "window": window,
        "causal_contract": {
            "context": "completed kline closes and completed 4h/day/week levels",
            "signal": "completed ten-second confirmation only",
            "multiasset": "decision after all four same-time bars",
        },
        "assets": {},
    }

    for asset in config["assets"]:
        symbol = str(asset["symbol"])
        instrument = _instrument(asset, fee)
        instruments.append(instrument)
        context_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
        execution_type = BarType.from_str(f"{instrument.id}-10-SECOND-LAST-EXTERNAL")
        bar_types.append(execution_type)

        one_minute = load_official_binance_bars(
            symbol=symbol,
            interval="1m",
            load_start=context_start,
            load_end=post_end,
            bar_type=context_type,
            instrument=instrument,
            cache_dir=cache / "klines",
        )
        loaded_agg = _load_agg(symbol, start, post_end, cache / "aggTrades")
        agg_frame, agg_sources, agg_quality = _agg_parts(loaded_agg)
        context_times, context_bars, snapshots = _context(one_minute.frame, pattern)
        bundle = build_acceptance_signals(
            symbol=symbol,
            data=agg_frame,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
            tick=float(asset["price_increment"]),
            minimum_net_reward_risk=float(config["minimum_net_reward_risk"]),
        )
        in_window = {
            timestamp: items
            for timestamp, items in bundle.signals_by_time_ns.items()
            if _ns(start) <= timestamp < _ns(end)
        }
        for timestamp, items in in_window.items():
            signals.setdefault(timestamp, []).extend(items)
        diagnostics[symbol] = {
            "signals_loaded": bundle.signal_count,
            "signals_in_window": sum(len(items) for items in in_window.values()),
            "reason_counts": bundle.diagnostics,
        }
        execution_bars = _bars(agg_frame, execution_type, instrument)
        all_data.extend(execution_bars)
        manifest["assets"][symbol] = {
            "instrument_id": str(instrument.id),
            "one_minute_quality": one_minute.quality,
            "one_minute_sources": [_source_dict(item) for item in one_minute.source_files],
            "aggtrade_quality": agg_quality,
            "aggtrade_sources": [_source_dict(item) for item in agg_sources],
            "execution_bars": len(execution_bars),
        }

    immutable_signals = {
        timestamp: tuple(sorted(items, key=lambda item: (-item.net_reward_risk, item.symbol)))
        for timestamp, items in sorted(signals.items())
    }
    all_data.sort(key=lambda bar: (int(bar.ts_event), str(bar.bar_type.instrument_id)))
    manifest_path = output / "data_manifest.json"
    write_json_atomic(manifest_path, _json_safe(manifest))

    strategy = SharedAcceptanceStrategy(
        SharedAcceptanceStrategyConfig(
            instrument_ids=tuple(item.id for item in instruments),
            bar_types=tuple(bar_types),
            trading_start_ns=_ns(start),
            trading_end_ns=_ns(end),
            risk_fraction=Decimal(str(config["risk_fraction"])),
            effective_fee_rate=fee,
            minimum_net_reward_risk=Decimal(str(config["minimum_net_reward_risk"])),
            maximum_hold_ns=int(config["maximum_hold_minutes"]) * 60 * 1_000_000_000,
            funding_avoidance_minutes=int(config["funding_avoidance_minutes"]),
        ),
        signals_by_time_ns=immutable_signals,
    )

    engine = _create_engine(_engine_config(config), instruments[0])
    for instrument in instruments[1:]:
        engine.add_instrument(instrument)
    try:
        engine.add_data(all_data)
        engine.add_strategy(strategy)
        engine.run()
        account = engine.cache.account_for_venue(Venue("BINANCE"))
        if account is None:
            raise RuntimeError("shared Binance margin account missing")
        cached_orders = engine.cache.orders()
        cached_positions = engine.cache.positions()
        orders = ReportProvider.generate_orders_report(cached_orders)
        fills = ReportProvider.generate_fills_report(cached_orders)
        positions = ReportProvider.generate_positions_report(cached_positions)
        account_report = ReportProvider.generate_account_report(account)
        orders.to_csv(output / "orders.csv", index=True)
        fills.to_csv(output / "fills.csv", index=True)
        positions.to_csv(output / "positions.csv", index=True)
        account_report.to_csv(output / "account.csv", index=True)

        money = account.balance_total(Currency.from_str("USDT"))
        if money is None:
            raise RuntimeError("shared USDT balance missing")
        starting_nav = float(config["starting_nav_usdt"])
        final_nav = float(money.as_double())
        days = (end - start).total_seconds() / 86400.0
        daily_growth = -1.0 if final_nav <= 0 else exp((log(final_nav) - log(starting_nav)) / days) - 1.0
        position_metrics, enriched = _position_metrics(
            positions, strategy.trade_intents, strategy.position_outcomes,
        )
        drawdown, equity = _equity_drawdown(account_report, starting_nav)
        open_positions = sum(
            len(engine.cache.positions_open(instrument_id=item.id)) for item in instruments
        )
        open_orders = sum(
            len(engine.cache.orders_open(instrument_id=item.id)) for item in instruments
        )
        result = engine.get_result()
        metrics = {
            "candidate": config["candidate"],
            "engine": "NautilusTrader",
            "window": window,
            "calendar_days": days,
            "starting_nav_usdt": starting_nav,
            "final_nav_usdt": final_nav,
            "nav_multiple": final_nav / starting_nav,
            "total_return": final_nav / starting_nav - 1.0,
            "daily_geometric_growth": daily_growth,
            "goal_daily_geometric_growth": 0.01,
            "goal_met_in_window": daily_growth >= 0.01,
            "maximum_realized_equity_drawdown": drawdown,
            "signals_in_window": sum(len(items) for items in immutable_signals.values()),
            "signals_by_asset": diagnostics,
            "orders": int(len(orders.index)),
            "fills": int(len(fills.index)),
            "trade_intents": len(strategy.trade_intents),
            "skipped_setups": len(strategy.skipped_setups),
            "execution_failures": len(strategy.execution_failures),
            "incomplete_multiasset_timestamps": len(strategy.incomplete_timestamps),
            "open_positions_after_run": open_positions,
            "open_orders_after_run": open_orders,
            "unexpected_or_liquidation_closes": sum(
                item.get("close_reason") == "UNEXPECTED_CLOSE_OR_LIQUIDATION"
                for item in strategy.position_outcomes
            ),
            "position_metrics": position_metrics,
            "nautilus_result": {
                "iterations": result.iterations,
                "total_events": result.total_events,
                "total_orders": result.total_orders,
                "total_positions": result.total_positions,
                "stats_pnls": result.stats_pnls,
                "stats_returns": result.stats_returns,
            },
            "cost_assumptions": config["cost_assumptions"],
        }
        if open_positions or open_orders:
            raise RuntimeError(
                f"residual exposure positions={open_positions}, orders={open_orders}"
            )
        gate = config["first_gate"]
        checks = {
            "minimum_closed_trades": int(position_metrics["closed_trades"]) >= int(gate["minimum_closed_trades"]),
            "cost_after_positive": float(metrics["total_return"]) > 0,
            "no_execution_failures": len(strategy.execution_failures) == 0,
            "no_residual_exposure": open_positions == 0 and open_orders == 0,
            "complete_multiasset_timestamps": len(strategy.incomplete_timestamps) == 0,
        }
        summary = {
            "candidate": config["candidate"],
            "suite": "first",
            "window_results": [{
                "name": window["name"],
                "signals": metrics["signals_in_window"],
                "closed_trades": position_metrics["closed_trades"],
                "wins": position_metrics["wins"],
                "win_rate": position_metrics["win_rate"],
                "final_nav_usdt": final_nav,
                "total_return": metrics["total_return"],
                "daily_geometric_growth": daily_growth,
                "maximum_realized_equity_drawdown": drawdown,
            }],
            "combined_daily_geometric_growth": daily_growth,
            "goal_daily_geometric_growth": 0.01,
            "goal_met": daily_growth >= 0.01,
            "gate_checks": checks,
            "gate_passed": all(checks.values()),
        }
        write_json_atomic(output / "metrics.json", _json_safe(metrics))
        write_json_atomic(output / "suite_metrics.json", _json_safe(summary))
        write_json_atomic(output / "trade_intents.json", {"trade_intents": _json_safe(strategy.trade_intents)})
        write_json_atomic(
            output / "position_outcomes.json",
            {"strategy_callbacks": _json_safe(strategy.position_outcomes), "enriched_positions": _json_safe(enriched)},
        )
        write_json_atomic(output / "skipped_setups.json", {"skipped_setups": _json_safe(strategy.skipped_setups)})
        write_json_atomic(output / "execution_failures.json", {"execution_failures": _json_safe(strategy.execution_failures)})
        write_json_atomic(output / "incomplete_timestamps.json", {"timestamps": strategy.incomplete_timestamps})
        write_json_atomic(output / "equity_curve.json", {"points": equity})
        with (output / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
            for event in strategy.events:
                stream.write(json.dumps(_json_safe(event), sort_keys=True) + "\n")
        write_json_atomic(
            output / "run.json",
            _json_safe(create_run_manifest(
                run_id="candidate-08-aggtrade-acceptance-nautilus-first",
                candidate=str(config["candidate"]),
                config_path=config_path,
                data_manifest_path=manifest_path,
                extra={"summary": summary, "shared_account": True, "global_position_limit": 1},
            )),
        )
        print(json.dumps(_json_safe(summary), indent=2, sort_keys=True), flush=True)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
