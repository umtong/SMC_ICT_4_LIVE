"""Reproducible NautilusTrader runner for candidate-08.

The strategy logic is imported from ``logic.py``.  This runner only wires official
Binance Vision bars into NautilusTrader's real backtest, order, margin, liquidation,
fee, fill, portfolio, and reporting components.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from math import exp, isfinite, log
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping

import pandas as pd

from nautilus_trader.analysis.reporter import ReportProvider
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.backtest.models import LatencyModel, MakerTakerFeeModel, OneTickSlippageFillModel
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

# Candidate files live beside this runner.  Keeping this explicit makes direct
# ``python research/candidate-08/run.py`` execution reproducible in Actions/Codespaces.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data import LoadedBars, load_official_binance_bars  # noqa: E402
from logic import LogicConfig, LogicEvent, group_events_by_reason  # noqa: E402
from strategy import Candidate08Strategy, Candidate08StrategyConfig  # noqa: E402
from smc_ict_4.contracts import ResearchEvent  # noqa: E402
from smc_ict_4.event_log import write_events  # noqa: E402
from smc_ict_4.manifest import create_run_manifest, sha256_file, write_json_atomic  # noqa: E402


MONEY_PATTERN = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _safe_float(value: Any) -> float | None:
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "as_double"):
        number = float(value.as_double())
    elif isinstance(value, (float, int, Decimal)):
        number = float(value)
    else:
        match = MONEY_PATTERN.match(str(value))
        if not match:
            return None
        number = float(match.group(1))
    return number if isfinite(number) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _build_instrument(config: Mapping[str, Any]) -> CryptoPerpetual:
    """Build a linear BTCUSDT perpetual with conservative all-in fee charging.

    ``margin_init=1`` combined with venue leverage 125 means the default leveraged
    margin model requires 1/125 initial notional margin.  It avoids double-applying
    the exchange leverage to a pre-scaled margin fraction.
    """

    usdt = Currency.from_str("USDT")
    btc = Currency.from_str("BTC")
    fee = Decimal(str(config["effective_fee_rate_per_fill"]))
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(str(config["instrument_id"])),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=btc,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=1,
        price_increment=Price.from_str("0.1"),
        size_precision=3,
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("1000.000"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=Money(10.0, usdt),
        max_price=Price.from_str("1000000.0"),
        min_price=Price.from_str("0.1"),
        margin_init=Decimal("1.0"),
        margin_maint=Decimal("0.5"),
        maker_fee=fee,
        taker_fee=fee,
        ts_event=0,
        ts_init=0,
        info={
            "source": "candidate-08 pinned research specification",
            "fee_contains_execution_funding_reserve": True,
        },
    )


def _data_manifest(loaded: LoadedBars, *, window: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "dataset": "Binance Vision USD-M BTCUSDT 1m klines",
        "window": dict(window),
        "timestamp_contract": {
            "source_open_time": "market interval start",
            "event_and_observed_time": "source close_time; bar unavailable before close",
        },
        "quality": loaded.quality,
        "files": [asdict(source) for source in loaded.source_files],
    }


def _logic_events_to_research(events: Iterable[LogicEvent], instrument_id: str) -> list[ResearchEvent]:
    converted: list[ResearchEvent] = []
    last_observed = -1
    for event in events:
        if event.observed_time_ns < last_observed:
            raise RuntimeError(
                "scenario events were not emitted in observation order: "
                f"{event.observed_time_ns} < {last_observed}"
            )
        last_observed = event.observed_time_ns
        converted.append(
            ResearchEvent(
                scenario_id=event.scenario_id,
                instrument_id=instrument_id,
                event_type=event.event_type,
                event_time_ns=event.event_time_ns,
                observed_time_ns=event.observed_time_ns,
                previous_state=event.previous_state,
                next_state=event.next_state,
                reason_code=event.reason_code,
                reference_price=(
                    None if event.reference_price is None else format(event.reference_price, ".12g")
                ),
                details=event.details,
            )
        )
    return converted


def _frame_for_analysis(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if result.index.name and result.index.name not in result.columns:
        result = result.reset_index()
    elif result.index.name is None:
        result = result.reset_index(drop=False)
    return result


def _closed_positions(positions: pd.DataFrame) -> pd.DataFrame:
    if positions.empty:
        return positions.copy()
    frame = _frame_for_analysis(positions)
    if "ts_closed" in frame.columns:
        return frame.loc[frame["ts_closed"].notna()].copy()
    if "side" in frame.columns:
        return frame.loc[frame["side"].astype(str).str.upper().eq("FLAT")].copy()
    return frame.copy()


def _equity_drawdown(account_report: pd.DataFrame, starting_nav: float) -> tuple[float, list[dict[str, Any]]]:
    values: list[tuple[str, float]] = [("INITIAL", starting_nav)]
    if not account_report.empty and "total" in account_report.columns:
        frame = _frame_for_analysis(account_report)
        if "currency" in frame.columns:
            frame = frame.loc[frame["currency"].astype(str).eq("USDT")]
        time_column = next(
            (name for name in ("ts_event", "index") if name in frame.columns),
            None,
        )
        for _, row in frame.iterrows():
            total = _safe_float(row.get("total"))
            if total is None:
                continue
            timestamp = str(row.get(time_column)) if time_column else "UNKNOWN"
            values.append((timestamp, total))
    peak = starting_nav
    max_drawdown = 0.0
    curve: list[dict[str, Any]] = []
    for timestamp, nav in values:
        peak = max(peak, nav)
        drawdown = nav / peak - 1.0 if peak > 0 else 0.0
        max_drawdown = min(max_drawdown, drawdown)
        curve.append({"timestamp": timestamp, "nav": nav, "drawdown": drawdown})
    return abs(max_drawdown), curve


def _position_metrics(
    positions: pd.DataFrame,
    intents: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    closed = _closed_positions(positions)
    intent_by_entry = {str(item["entry_order_id"]): item for item in intents}
    outcome_by_position = {str(item["position_id"]): item for item in outcomes}
    enriched: list[dict[str, Any]] = []
    pnl_values: list[float] = []
    by_family: dict[str, list[float]] = {}

    for _, row in closed.iterrows():
        row_dict = {str(key): _json_safe(value) for key, value in row.to_dict().items()}
        position_id = str(row_dict.get("position_id", row.name))
        opening_order_id = str(row_dict.get("opening_order_id", ""))
        intent = intent_by_entry.get(opening_order_id, {})
        outcome = outcome_by_position.get(position_id, {})
        pnl = _safe_float(row.get("realized_pnl"))
        if pnl is None:
            pnl = 0.0
        pnl_values.append(pnl)
        family = str(intent.get("scenario_family", outcome.get("scenario_family", "UNKNOWN")))
        by_family.setdefault(family, []).append(pnl)
        enriched.append(
            {
                **row_dict,
                "position_id": position_id,
                "scenario_id": intent.get("scenario_id", outcome.get("scenario_id")),
                "scenario_family": family,
                "direction": intent.get("direction", outcome.get("direction")),
                "planned_stop_loss": intent.get("planned_stop_loss"),
                "risk_budget": intent.get("risk_budget"),
                "net_reward_risk_at_signal": intent.get("net_reward_risk"),
                "close_reason": outcome.get("close_reason"),
                "realized_pnl_numeric": pnl,
            }
        )

    positive = [value for value in pnl_values if value > 0]
    single_positive_share = max(positive) / sum(positive) if positive and sum(positive) > 0 else 0.0
    family_metrics = {
        family: {
            "trades": len(values),
            "wins": sum(value > 0 for value in values),
            "realized_pnl": sum(values),
            "win_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
        }
        for family, values in sorted(by_family.items())
    }
    metrics = {
        "closed_trades": len(pnl_values),
        "wins": sum(value > 0 for value in pnl_values),
        "losses": sum(value < 0 for value in pnl_values),
        "flat_trades": sum(value == 0 for value in pnl_values),
        "win_rate": sum(value > 0 for value in pnl_values) / len(pnl_values) if pnl_values else 0.0,
        "realized_pnl_from_positions": sum(pnl_values),
        "average_trade_pnl": sum(pnl_values) / len(pnl_values) if pnl_values else 0.0,
        "largest_win": max(pnl_values) if pnl_values else 0.0,
        "largest_loss": min(pnl_values) if pnl_values else 0.0,
        "single_trade_positive_pnl_share": single_positive_share,
        "scenario_families": family_metrics,
    }
    return metrics, enriched


def _create_engine(config: Mapping[str, Any], instrument: CryptoPerpetual) -> BacktestEngine:
    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    seed = int(config["random_seed"])
    cost = config["cost_assumptions"]
    latency = cost["latency_ms"]
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
            run_analysis=True,
        )
    )
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(float(config["starting_nav_usdt"]), usdt)],
        base_currency=usdt,
        default_leverage=Decimal(str(config["venue"]["default_leverage"])),
        fill_model=OneTickSlippageFillModel(
            prob_fill_on_limit=float(cost["limit_fill_probability"]),
            prob_slippage=float(cost["one_tick_slippage_probability"]),
            random_seed=seed,
        ),
        fee_model=MakerTakerFeeModel(),
        latency_model=LatencyModel(
            base_latency_nanos=int(latency["base"] * 1_000_000),
            insert_latency_nanos=int(latency["insert"] * 1_000_000),
            update_latency_nanos=int(latency["update"] * 1_000_000),
            cancel_latency_nanos=int(latency["cancel"] * 1_000_000),
        ),
        reject_stop_orders=False,
        support_contingent_orders=True,
        use_position_ids=True,
        use_reduce_only=True,
        bar_execution=True,
        bar_adaptive_high_low_ordering=bool(
            config["venue"]["bar_adaptive_high_low_ordering"]
        ),
    )
    engine.add_instrument(instrument)
    return engine


def run_window(
    *,
    config: Mapping[str, Any],
    config_path: Path,
    window: Mapping[str, Any],
    output_dir: Path,
    data_cache: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    start = _parse_utc(str(window["start"]))
    end = _parse_utc(str(window["end"]))
    if end <= start:
        raise ValueError(f"invalid evaluation window: {window}")
    # Two complete days make all indicators and one-day liquidity pools causal before trading starts.
    load_start = start - timedelta(days=2)
    # Extra bars let an evaluation-end market close and its order/fill callbacks complete.
    load_end = end + timedelta(minutes=10)

    instrument = _build_instrument(config)
    bar_type = BarType.from_str(str(config["bar_type"]))
    loaded = load_official_binance_bars(
        symbol="BTCUSDT",
        interval="1m",
        load_start=load_start,
        load_end=load_end,
        bar_type=bar_type,
        instrument=instrument,
        cache_dir=data_cache,
    )
    data_manifest = _data_manifest(loaded, window=window)
    data_manifest_path = output_dir / "data_manifest.json"
    write_json_atomic(data_manifest_path, _json_safe(data_manifest))

    logic_config = LogicConfig.from_mapping(dict(config["logic"]))
    strategy = Candidate08Strategy(
        Candidate08StrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            trading_start_ns=_ns(start),
            trading_end_ns=_ns(end),
            risk_fraction=Decimal(str(config["risk_fraction"])),
            effective_fee_rate=Decimal(str(config["effective_fee_rate_per_fill"])),
            minimum_net_reward_risk=Decimal(str(config["logic"]["minimum_net_reward_risk"])),
            maximum_hold_bars=int(config["logic"]["maximum_hold_bars"]),
            funding_avoidance_minutes=int(config["logic"]["funding_avoidance_minutes"]),
        ),
        logic_config=logic_config,
    )

    engine = _create_engine(config, instrument)
    try:
        engine.add_data(loaded.bars)
        engine.add_strategy(strategy)
        engine.run()

        account = engine.cache.account_for_venue(Venue("BINANCE"))
        if account is None:
            raise RuntimeError("NautilusTrader did not retain the Binance margin account")
        cached_orders = engine.cache.orders()
        cached_positions = engine.cache.positions()
        orders = ReportProvider.generate_orders_report(cached_orders)
        fills = ReportProvider.generate_fills_report(cached_orders)
        positions = ReportProvider.generate_positions_report(cached_positions)
        account_report = ReportProvider.generate_account_report(account)
        orders.to_csv(output_dir / "orders.csv", index=True)
        fills.to_csv(output_dir / "fills.csv", index=True)
        positions.to_csv(output_dir / "positions.csv", index=True)
        account_report.to_csv(output_dir / "account.csv", index=True)

        final_money = account.balance_total(Currency.from_str("USDT"))
        if final_money is None:
            raise RuntimeError("NautilusTrader account had no total USDT balance")
        starting_nav = float(config["starting_nav_usdt"])
        final_nav = float(final_money.as_double())
        days = (end - start).total_seconds() / 86_400.0
        if final_nav <= 0:
            geometric = -1.0
        else:
            geometric = exp((log(final_nav) - log(starting_nav)) / days) - 1.0

        position_metrics, enriched_positions = _position_metrics(
            positions,
            strategy.trade_intents,
            strategy.position_outcomes,
        )
        max_drawdown, equity_curve = _equity_drawdown(account_report, starting_nav)
        open_positions = len(engine.cache.positions_open(instrument_id=instrument.id))
        open_orders = len(engine.cache.orders_open(instrument_id=instrument.id))
        result = engine.get_result()
        metrics: dict[str, Any] = {
            "candidate": config["candidate"],
            "window": dict(window),
            "calendar_days": days,
            "starting_nav_usdt": starting_nav,
            "final_nav_usdt": final_nav,
            "nav_multiple": final_nav / starting_nav,
            "total_return": final_nav / starting_nav - 1.0,
            "daily_geometric_growth": geometric,
            "goal_daily_geometric_growth": 0.01,
            "goal_met_in_window": geometric >= 0.01,
            "maximum_realized_equity_drawdown": max_drawdown,
            "orders": int(len(orders.index)),
            "fills": int(len(fills.index)),
            "open_positions_after_run": open_positions,
            "open_orders_after_run": open_orders,
            "trade_intents": len(strategy.trade_intents),
            "skipped_setups": len(strategy.skipped_setups),
            "execution_failures": len(strategy.execution_failures),
            "unexpected_or_liquidation_closes": sum(
                item.get("close_reason") == "UNEXPECTED_CLOSE_OR_LIQUIDATION"
                for item in strategy.position_outcomes
            ),
            "logic_event_count": len(strategy.logic.events),
            "logic_reason_counts": group_events_by_reason(strategy.logic.events),
            "position_metrics": position_metrics,
            "nautilus_result": {
                "iterations": result.iterations,
                "total_events": result.total_events,
                "total_orders": result.total_orders,
                "total_positions": result.total_positions,
                "stats_pnls": result.stats_pnls,
                "stats_returns": result.stats_returns,
                "stats_general": result.stats_general,
            },
            "data_quality": loaded.quality,
            "cost_assumptions": config["cost_assumptions"],
        }
        if open_positions or open_orders:
            raise RuntimeError(
                f"run ended with exposure: open_positions={open_positions}, open_orders={open_orders}"
            )
        if strategy.execution_failures:
            metrics["execution_failure_details"] = strategy.execution_failures

        write_json_atomic(output_dir / "metrics.json", _json_safe(metrics))
        write_json_atomic(
            output_dir / "trade_intents.json",
            {"trade_intents": _json_safe(strategy.trade_intents)},
        )
        write_json_atomic(
            output_dir / "position_outcomes.json",
            {
                "strategy_callbacks": _json_safe(strategy.position_outcomes),
                "enriched_positions": _json_safe(enriched_positions),
            },
        )
        write_json_atomic(
            output_dir / "skipped_setups.json",
            {"skipped_setups": _json_safe(strategy.skipped_setups)},
        )
        write_json_atomic(output_dir / "equity_curve.json", {"points": equity_curve})
        write_events(
            output_dir / "scenario_events.jsonl",
            _logic_events_to_research(strategy.logic.events, str(instrument.id)),
        )

        run_id = f"candidate-08-{window['name']}"
        run_manifest = create_run_manifest(
            run_id=run_id,
            candidate="candidate-08",
            config_path=config_path,
            data_manifest_path=data_manifest_path,
            extra={
                "window": dict(window),
                "nautilus_run_config": {
                    "engine": "BacktestEngine",
                    "oms_type": "HEDGING",
                    "account_type": "MARGIN",
                    "bar_adaptive_high_low_ordering": config["venue"][
                        "bar_adaptive_high_low_ordering"
                    ],
                    "liquidation_enabled": config["venue"]["liquidation_enabled"],
                },
                "result_summary": {
                    "final_nav_usdt": final_nav,
                    "daily_geometric_growth": geometric,
                    "closed_trades": position_metrics["closed_trades"],
                },
            },
        )
        write_json_atomic(output_dir / "run.json", _json_safe(run_manifest))
        return metrics
    finally:
        engine.dispose()


def _suite_metrics(
    *,
    config: Mapping[str, Any],
    suite: str,
    windows: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    total_days = sum(float(item["calendar_days"]) for item in results)
    total_log_growth = sum(
        log(float(item["nav_multiple"])) for item in results if float(item["nav_multiple"]) > 0
    )
    combined_daily_growth = exp(total_log_growth / total_days) - 1.0 if total_days else 0.0
    closed = [int(item["position_metrics"]["closed_trades"]) for item in results]
    positive_windows = sum(float(item["total_return"]) > 0 for item in results)
    families: dict[str, float] = {}
    for item in results:
        for family, family_metrics in item["position_metrics"]["scenario_families"].items():
            families[family] = families.get(family, 0.0) + float(family_metrics["realized_pnl"])

    gate = config["screen_gate"]
    screen_gate_checks = {
        "minimum_closed_trades_each_week": (
            all(value >= int(gate["minimum_closed_trades_per_week"]) for value in closed)
            if suite == "screen"
            else None
        ),
        "minimum_positive_weeks": (
            positive_windows >= int(gate["minimum_positive_weeks"]) if suite == "screen" else None
        ),
        "minimum_profitable_scenario_families": (
            sum(value > 0 for value in families.values())
            >= int(gate["minimum_profitable_scenario_families"])
            if suite == "screen"
            else None
        ),
        "maximum_single_trade_positive_pnl_share": (
            all(
                float(item["position_metrics"]["single_trade_positive_pnl_share"])
                <= float(gate["maximum_single_trade_positive_pnl_share"])
                for item in results
            )
            if suite == "screen"
            else None
        ),
        "all_windows_cost_after_positive": (
            all(float(item["total_return"]) > 0 for item in results) if suite == "screen" else None
        ),
        "no_execution_failures": all(int(item["execution_failures"]) == 0 for item in results),
        "no_residual_exposure": all(
            int(item["open_positions_after_run"]) == 0 and int(item["open_orders_after_run"]) == 0
            for item in results
        ),
    }
    applicable = [value for value in screen_gate_checks.values() if value is not None]
    screen_passed = bool(applicable) and all(applicable) if suite == "screen" else None
    return {
        "candidate": config["candidate"],
        "suite": suite,
        "predeclared_windows": windows,
        "window_results": [
            {
                "name": item["window"]["name"],
                "starting_nav_usdt": item["starting_nav_usdt"],
                "final_nav_usdt": item["final_nav_usdt"],
                "total_return": item["total_return"],
                "daily_geometric_growth": item["daily_geometric_growth"],
                "closed_trades": item["position_metrics"]["closed_trades"],
                "win_rate": item["position_metrics"]["win_rate"],
                "maximum_realized_equity_drawdown": item["maximum_realized_equity_drawdown"],
            }
            for item in results
        ],
        "combined_calendar_days": total_days,
        "combined_daily_geometric_growth": combined_daily_growth,
        "goal_daily_geometric_growth": 0.01,
        "goal_met": combined_daily_growth >= 0.01,
        "positive_windows": positive_windows,
        "closed_trades_by_window": closed,
        "scenario_family_realized_pnl": families,
        "screen_gate_checks": screen_gate_checks,
        "screen_gate_passed": screen_passed,
    }


def run_suite(
    *,
    config_path: Path,
    suite: str,
    output: Path,
    data_cache: Path,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    windows = list(config["suites"][suite])
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for window in windows:
        destination = output / str(window["name"])
        result = run_window(
            config=config,
            config_path=config_path,
            window=window,
            output_dir=destination,
            data_cache=data_cache,
        )
        results.append(result)
        print(
            json.dumps(
                {
                    "window": window["name"],
                    "daily_geometric_growth": result["daily_geometric_growth"],
                    "final_nav_usdt": result["final_nav_usdt"],
                    "closed_trades": result["position_metrics"]["closed_trades"],
                    "win_rate": result["position_metrics"]["win_rate"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    suite_metrics = _suite_metrics(
        config=config,
        suite=suite,
        windows=windows,
        results=results,
    )
    write_json_atomic(output / "suite_metrics.json", _json_safe(suite_metrics))
    write_json_atomic(
        output / "run.json",
        _json_safe(
            create_run_manifest(
                run_id=f"candidate-08-{suite}",
                candidate="candidate-08",
                config_path=config_path,
                extra={
                    "suite": suite,
                    "config_sha256": sha256_file(config_path),
                    "summary": suite_metrics,
                },
            )
        ),
    )
    print(json.dumps(_json_safe(suite_metrics), indent=2, sort_keys=True), flush=True)
    return suite_metrics


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("screen", "long"), default="screen")
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data-cache",
        type=Path,
        default=Path.home() / ".cache" / "smc4" / "candidate-08" / "binance-vision",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run_suite(
        config_path=args.config.resolve(),
        suite=args.suite,
        output=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
