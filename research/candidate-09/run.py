#!/usr/bin/env python3
"""Run candidate-09 with NautilusTrader and emit reproducible evidence.

No search or parameter optimizer is present.  The structurally frozen v2 baseline and three
single-variable ablations run on the same predeclared BTC weeks.  The three-year
monthly evaluation is allowed only after the gate passes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data_loader import BinanceVisionCache, load_fixed_weeks, load_monthly_range, write_manifest
from nautilus_strategy import Candidate09Strategy, Candidate09StrategyConfig
from state_engine import EngineConfig, FlowBar


ABLATIONS = (
    "baseline",
    "no-flow",
    "no-mss-confirmation",
    "no-acceptance-confirmation",
)
DAY_NS = 86_400_000_000_000


@dataclass(frozen=True, slots=True)
class RunOutcome:
    run_id: str
    variant: str
    segment: str
    start_ns: int
    end_ns: int
    calendar_days: float
    starting_nav: float
    ending_nav: float
    total_return: float
    daily_geometric_return: float
    trades: int
    wins: int
    losses: int
    win_rate: float | None
    profit_factor: float | None
    expectancy_r: float | None
    max_drawdown: float
    maximum_consecutive_losses: int
    largest_profit_share: float | None
    reversal_trades: int
    continuation_trades: int
    rejected_orders: int
    time_exits: int
    missing_feature_bars: int
    open_position_at_stop: bool
    native_account_final: float | None
    native_expected_final: float
    accounting_error: float | None
    implementation_status: str


@dataclass(slots=True)
class DetailedRun:
    outcome: RunOutcome
    events: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    fills: list[dict[str, Any]]


def read_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def git_value(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=HERE, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def bars_to_frame(bars: Sequence[FlowBar]) -> pd.DataFrame:
    if not bars:
        raise ValueError("cannot build a Nautilus frame from no bars")
    index = pd.to_datetime([bar.ts_ns for bar in bars], unit="ns", utc=True)
    frame = pd.DataFrame(
        {
            "open": [bar.open for bar in bars],
            "high": [bar.high for bar in bars],
            "low": [bar.low for bar in bars],
            "close": [bar.close for bar in bars],
            "volume": [bar.volume for bar in bars],
        },
        index=index,
    )
    frame.index.name = "timestamp"
    return frame


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if hasattr(value, "as_double"):
        return float(value.as_double())
    if hasattr(value, "as_decimal"):
        return float(value.as_decimal())
    if isinstance(value, Mapping):
        for item in value.values():
            parsed = _as_float(item)
            if parsed is not None:
                return parsed
        return None
    try:
        return float(str(value).split()[0])
    except (TypeError, ValueError):
        return None


def account_total(strategy: Candidate09Strategy) -> float | None:
    try:
        account = strategy.cache.account_for_venue(strategy.config.instrument_id.venue)
    except Exception:
        return None
    if account is None:
        return None
    for name in ("balance_total", "total_balance"):
        method = getattr(account, name, None)
        if method is None:
            continue
        for args in ((USDT,), tuple()):
            try:
                result = method(*args)
            except Exception:
                continue
            parsed = _as_float(result)
            if parsed is not None:
                return parsed
    return None


def run_nautilus_segment(
    *,
    config: Mapping[str, Any],
    bars: Sequence[FlowBar],
    segment: str,
    variant: str,
) -> DetailedRun:
    if len(bars) < 2:
        raise ValueError(f"segment {segment} has too few bars")
    starting_nav = float(config["risk"]["starting_nav_usdt"])
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    # Construct native Nautilus bars directly.  This avoids a pandas copy-on-write
    # incompatibility in BarDataWrangler while preserving the same close-time
    # ts_event/ts_init contract and all Nautilus execution/accounting semantics.
    nautilus_bars = [
        Bar(
            bar_type=bar_type,
            open=instrument.make_price(item.open),
            high=instrument.make_price(item.high),
            low=instrument.make_price(item.low),
            close=instrument.make_price(item.close),
            volume=instrument.make_qty(item.volume),
            ts_event=item.ts_ns,
            ts_init=item.ts_ns,
        )
        for item in bars
    ]
    flow_map = {int(nautilus_bar.ts_init): flow_bar for nautilus_bar, flow_bar in zip(nautilus_bars, bars)}
    events: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("CANDIDATE-09"),
            logging=LoggingConfig(bypass_logging=True),
        ),
    )
    engine.add_venue(
        venue=instrument.id.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USDT,
        starting_balances=[Money.from_str(f"{starting_nav:.2f} USDT")],
        default_leverage=Decimal("100"),
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
    )
    engine.add_instrument(instrument)
    engine.add_data(nautilus_bars)

    engine_config = EngineConfig.from_mapping(config, ablation=variant)
    strategy_config = Candidate09StrategyConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
        risk_fraction=float(config["risk"]["risk_fraction"]),
        starting_nav=starting_nav,
        composite_cost_per_fill=float(config["risk"]["composite_taker_cost_per_fill"]),
        maximum_holding_bars=int(config["trade"]["maximum_holding_minutes"]),
        flat_before_utc_midnight_minutes=int(config["trade"]["flat_before_utc_midnight_minutes"]),
    )
    strategy = Candidate09Strategy(
        strategy_config,
        engine_config=engine_config,
        flow_bars=flow_map,
        diagnostic_events=events,
        trade_records=trades,
        fill_records=fills,
    )
    engine.add_strategy(strategy)
    try:
        engine.run()
        native_final = account_total(strategy)
        open_at_stop = not strategy.portfolio.is_flat(instrument.id)
        outcome = calculate_outcome(
            run_id=f"{variant}:{segment}",
            variant=variant,
            segment=segment,
            bars=bars,
            starting_nav=starting_nav,
            adjusted_final=strategy.adjusted_nav,
            native_final=native_final,
            trades=trades,
            rejected_orders=strategy.rejected_orders,
            time_exits=strategy.time_exits,
            missing_feature_bars=strategy.missing_feature_bars,
            open_position_at_stop=open_at_stop,
        )
    finally:
        engine.dispose()
    return DetailedRun(outcome, events, trades, fills)


def calculate_outcome(
    *,
    run_id: str,
    variant: str,
    segment: str,
    bars: Sequence[FlowBar],
    starting_nav: float,
    adjusted_final: float,
    native_final: float | None,
    trades: Sequence[Mapping[str, Any]],
    rejected_orders: int,
    time_exits: int,
    missing_feature_bars: int,
    open_position_at_stop: bool,
) -> RunOutcome:
    start_ns = bars[0].ts_ns - 60_000_000_000
    end_ns = bars[-1].ts_ns
    days = max((end_ns - start_ns) / DAY_NS, 1.0 / 1440.0)
    total_return = adjusted_final / starting_nav - 1.0
    daily_geo = (adjusted_final / starting_nav) ** (1.0 / days) - 1.0 if adjusted_final > 0.0 else -1.0
    pnls = [float(row["net_pnl"]) for row in trades]
    rs = [float(row["realized_r"]) for row in trades if row.get("realized_r") is not None]
    wins = sum(value > 0.0 for value in pnls)
    losses = sum(value < 0.0 for value in pnls)
    positive = sum(value for value in pnls if value > 0.0)
    negative = -sum(value for value in pnls if value < 0.0)
    profit_factor = positive / negative if negative > 0.0 else (math.inf if positive > 0.0 else None)
    largest_share = max((value for value in pnls if value > 0.0), default=0.0) / positive if positive > 0.0 else None
    max_consecutive = 0
    current_losses = 0
    for value in pnls:
        if value < 0.0:
            current_losses += 1
            max_consecutive = max(max_consecutive, current_losses)
        else:
            current_losses = 0
    nav_path = [starting_nav] + [float(row["nav_after"]) for row in trades]
    peak = nav_path[0]
    max_drawdown = 0.0
    for nav in nav_path:
        peak = max(peak, nav)
        if peak > 0.0:
            max_drawdown = max(max_drawdown, 1.0 - nav / peak)
    branches = Counter(str(row["branch"]) for row in trades)
    native_expected = starting_nav + sum(
        float(row["gross_realized_pnl"]) - float(row["native_commissions"])
        for row in trades
    )
    accounting_error = native_final - native_expected if native_final is not None else None
    implementation_status = "OK"
    if missing_feature_bars or open_position_at_stop:
        implementation_status = "IMPLEMENTATION_ERROR"
    if accounting_error is not None and abs(accounting_error) > max(0.05, starting_nav * 1e-7):
        implementation_status = "IMPLEMENTATION_ERROR"
    return RunOutcome(
        run_id=run_id,
        variant=variant,
        segment=segment,
        start_ns=start_ns,
        end_ns=end_ns,
        calendar_days=days,
        starting_nav=starting_nav,
        ending_nav=adjusted_final,
        total_return=total_return,
        daily_geometric_return=daily_geo,
        trades=len(trades),
        wins=wins,
        losses=losses,
        win_rate=wins / len(trades) if trades else None,
        profit_factor=profit_factor,
        expectancy_r=sum(rs) / len(rs) if rs else None,
        max_drawdown=max_drawdown,
        maximum_consecutive_losses=max_consecutive,
        largest_profit_share=largest_share,
        reversal_trades=branches["REVERSAL"],
        continuation_trades=branches["CONTINUATION"],
        rejected_orders=rejected_orders,
        time_exits=time_exits,
        missing_feature_bars=missing_feature_bars,
        open_position_at_stop=open_position_at_stop,
        native_account_final=native_final,
        native_expected_final=native_expected,
        accounting_error=accounting_error,
        implementation_status=implementation_status,
    )


def pooled_metrics(outcomes: Sequence[RunOutcome]) -> dict[str, Any]:
    if not outcomes:
        raise ValueError("cannot pool no outcomes")
    nav_multiple = math.prod(1.0 + outcome.total_return for outcome in outcomes)
    total_days = sum(outcome.calendar_days for outcome in outcomes)
    geo = nav_multiple ** (1.0 / total_days) - 1.0 if nav_multiple > 0.0 else -1.0
    total_trades = sum(outcome.trades for outcome in outcomes)
    return {
        "nav_multiple": nav_multiple,
        "calendar_days": total_days,
        "daily_geometric_return": geo,
        "trades": total_trades,
        "trades_per_day": total_trades / total_days,
        "all_segments_positive": all(outcome.total_return > 0.0 for outcome in outcomes),
        "maximum_segment_drawdown": max(outcome.max_drawdown for outcome in outcomes),
        "maximum_single_trade_profit_share": max(
            (outcome.largest_profit_share or 0.0) for outcome in outcomes
        ),
        "implementation_ok": all(outcome.implementation_status == "OK" for outcome in outcomes),
    }


def evaluate_gate(config: Mapping[str, Any], baseline: Sequence[RunOutcome]) -> tuple[bool, dict[str, Any]]:
    pooled = pooled_metrics(baseline)
    gate = config["gate"]
    checks = {
        "implementation_ok": pooled["implementation_ok"],
        "pooled_daily_geometric_return": pooled["daily_geometric_return"]
        >= float(gate["minimum_pooled_daily_geometric_return"]),
        "minimum_trades_each_week": all(
            outcome.trades >= int(gate["minimum_trades_per_week"]) for outcome in baseline
        ),
        "all_weeks_positive": (
            not bool(gate["require_all_weeks_positive"])
            or all(outcome.total_return > 0.0 for outcome in baseline)
        ),
        "profit_not_single_trade_dominated": all(
            outcome.largest_profit_share is None
            or outcome.largest_profit_share <= float(gate["maximum_single_trade_profit_share"])
            for outcome in baseline
        ),
    }
    return all(checks.values()), {"pooled": pooled, "checks": checks}


def diagnose_failure(
    baseline: Sequence[RunOutcome],
    ablations: Mapping[str, Sequence[RunOutcome]],
) -> dict[str, Any]:
    if any(outcome.implementation_status != "OK" for outcome in baseline):
        return {
            "classification": "IMPLEMENTATION_ERROR",
            "action": "Fix execution/accounting/time contracts and rerun the identical weeks before changing logic.",
            "largest_influence": "implementation contract",
        }
    base = pooled_metrics(baseline)
    comparisons = {
        name: pooled_metrics(outcomes)
        for name, outcomes in ablations.items()
        if name != "baseline"
    }
    best_name, best = max(
        comparisons.items(),
        key=lambda item: item[1]["daily_geometric_return"],
    )
    if best["daily_geometric_return"] > base["daily_geometric_return"] and best["nav_multiple"] > 1.0:
        return {
            "classification": "LOGIC_ERROR_WITH_STRUCTURAL_PATH",
            "action": f"The single-variable ablation {best_name} improved pooled cost-after growth; revise only that confirmation layer, then freeze and retest.",
            "largest_influence": best_name,
            "baseline_daily_geometric_return": base["daily_geometric_return"],
            "best_ablation_daily_geometric_return": best["daily_geometric_return"],
        }
    valid_parts = []
    reversal = sum(outcome.reversal_trades for outcome in baseline)
    continuation = sum(outcome.continuation_trades for outcome in baseline)
    if reversal:
        valid_parts.append("absorption/reclaim branch produced executable events")
    if continuation:
        valid_parts.append("acceptance/retest branch produced executable events")
    if base["maximum_segment_drawdown"] < 0.15:
        valid_parts.append("risk-budgeted loss path remained recoverable in the gate sample")
    return {
        "classification": "LOGIC_ERROR_NO_STRUCTURAL_PATH",
        "action": "Discard candidate-09 as a complete candidate; preserve only the listed mechanisms for later hypotheses.",
        "largest_influence": "insufficient cost-after conditional edge or opportunity rate",
        "baseline_daily_geometric_return": base["daily_geometric_return"],
        "best_ablation": best_name,
        "best_ablation_daily_geometric_return": best["daily_geometric_return"],
        "valid_parts": valid_parts,
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                keys.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(json_safe(row), sort_keys=True) + "\n")


def report_markdown(summary: Mapping[str, Any], outcomes: Sequence[RunOutcome]) -> str:
    gate = summary["gate"]
    diagnosis = summary.get("diagnosis")
    lines = [
        "# Candidate 09 reproducible evaluation",
        "",
        f"- Status: **{summary['status']}**",
        f"- Gate passed: **{gate['passed']}**",
        f"- Baseline pooled daily geometric return: **{gate['pooled']['daily_geometric_return']:.6%}**",
        f"- Baseline pooled NAV multiple across sampled days: **{gate['pooled']['nav_multiple']:.6f}x**",
        f"- Baseline trades: **{gate['pooled']['trades']}**",
        f"- Maximum sampled-segment drawdown: **{gate['pooled']['maximum_segment_drawdown']:.6%}**",
        "",
        "## Fixed-week results",
        "",
        "| week | return | daily geo | trades | win rate | PF | max DD | reversal | continuation | implementation |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for outcome in outcomes:
        pf = "n/a" if outcome.profit_factor is None else ("inf" if math.isinf(outcome.profit_factor) else f"{outcome.profit_factor:.3f}")
        wr = "n/a" if outcome.win_rate is None else f"{outcome.win_rate:.2%}"
        lines.append(
            f"| {outcome.segment} | {outcome.total_return:.4%} | {outcome.daily_geometric_return:.4%} | "
            f"{outcome.trades} | {wr} | {pf} | {outcome.max_drawdown:.4%} | "
            f"{outcome.reversal_trades} | {outcome.continuation_trades} | {outcome.implementation_status} |",
        )
    lines.extend(["", "## Gate checks", ""])
    for name, passed in gate["checks"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} — `{name}`")
    if diagnosis:
        lines.extend(
            [
                "",
                "## Failure classification / structural diagnosis",
                "",
                f"- Classification: **{diagnosis['classification']}**",
                f"- Largest influence: **{diagnosis['largest_influence']}**",
                f"- Required action: {diagnosis['action']}",
            ],
        )
        if diagnosis.get("valid_parts"):
            lines.append("- Parts worth preserving: " + "; ".join(diagnosis["valid_parts"]))
    lines.extend(
        [
            "",
            "## Known failure conditions",
            "",
            "1. The public kline archive supplies taker-buy flow but not historical L2 replenishment/cancellation; hidden absorption and spoofing can be misclassified.",
            "2. Nautilus bar execution uses adaptive OHLC ordering when both protective prices occur in one minute; trade-tick replay can change those fills.",
            "3. Slippage, impact and funding reserve are charged as an explicit cash-equivalent composite cost; nonlinear capacity impact is not inferred from one-minute bars.",
            "4. The test instrument's static margin model does not reproduce Binance notional tiers or every liquidation rule. Any rejected order is reported, never silently resized.",
            "5. Continuation is skipped when no already-observable opposing liquidity pool exists; price-discovery trends can be missed by design.",
            "6. A gate pass is not a final success: the frozen three-year BTC evaluation must also exceed the cost-after 1% daily geometric criterion without concentration.",
        ],
    )
    if summary.get("long_evaluation"):
        long_eval = summary["long_evaluation"]
        lines.extend(
            [
                "",
                "## Frozen long evaluation",
                "",
                f"- Status: **{long_eval['status']}**",
                f"- Daily geometric return: **{long_eval['outcome']['daily_geometric_return']:.6%}**",
                f"- NAV multiple: **{1.0 + long_eval['outcome']['total_return']:.6f}x**",
                f"- Trades: **{long_eval['outcome']['trades']}**",
                f"- Maximum drawdown: **{long_eval['outcome']['max_drawdown']:.6%}**",
            ],
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("gate", "long"), nargs="?", default="gate")
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=Path(os.environ.get("SMC4_DATA_ROOT", ".cache/candidate-09")))
    parser.add_argument("--output", type=Path, default=HERE / "evidence" / "latest")
    parser.add_argument("--auto-long", action="store_true")
    args = parser.parse_args()

    started = time.time()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = read_config(args.config)
    cache = BinanceVisionCache(args.cache, symbol=str(config["instrument"]), interval=str(config["bar_interval"]))
    all_details: list[DetailedRun] = []
    data_manifest: dict[str, Any]

    if args.mode == "long":
        spec = config["long_evaluation"]
        bars, data_manifest = load_monthly_range(
            start=date.fromisoformat(spec["start"]),
            end_exclusive=date.fromisoformat(spec["end_exclusive"]),
            cache=cache,
        )
        detail = run_nautilus_segment(config=config, bars=bars, segment="long-btc", variant="baseline")
        all_details.append(detail)
        pass_long = (
            detail.outcome.implementation_status == "OK"
            and detail.outcome.daily_geometric_return >= float(spec["success_daily_geometric_return"])
            and detail.outcome.largest_profit_share is not None
            and detail.outcome.largest_profit_share <= float(config["gate"]["maximum_single_trade_profit_share"])
        )
        summary = {
            "candidate": config["candidate"],
            "status": "SUCCESS" if pass_long else "FAILED_LONG_EVALUATION",
            "long_evaluation": {"status": "PASS" if pass_long else "FAIL", "outcome": asdict(detail.outcome)},
        }
        baseline_details = all_details
    else:
        weeks, data_manifest = load_fixed_weeks(config, cache)
        by_variant: dict[str, list[DetailedRun]] = {}
        for variant in ABLATIONS:
            by_variant[variant] = [
                run_nautilus_segment(config=config, bars=bars, segment=name, variant=variant)
                for name, bars in weeks.items()
            ]
            all_details.extend(by_variant[variant])
        baseline_details = by_variant["baseline"]
        baseline_outcomes = [detail.outcome for detail in baseline_details]
        gate_passed, gate_detail = evaluate_gate(config, baseline_outcomes)
        gate_payload = {"passed": gate_passed, **gate_detail}
        summary = {
            "candidate": config["candidate"],
            "status": "GATE_PASS" if gate_passed else "GATE_FAIL",
            "gate": gate_payload,
            "baseline_weeks": [asdict(outcome) for outcome in baseline_outcomes],
            "ablations": {
                variant: pooled_metrics([detail.outcome for detail in details])
                for variant, details in by_variant.items()
                if variant != "baseline"
            },
        }
        if not gate_passed:
            summary["diagnosis"] = diagnose_failure(
                baseline_outcomes,
                {variant: [detail.outcome for detail in details] for variant, details in by_variant.items()},
            )
        elif args.auto_long:
            spec = config["long_evaluation"]
            long_bars, long_manifest = load_monthly_range(
                start=date.fromisoformat(spec["start"]),
                end_exclusive=date.fromisoformat(spec["end_exclusive"]),
                cache=cache,
            )
            long_detail = run_nautilus_segment(
                config=config,
                bars=long_bars,
                segment="long-btc",
                variant="baseline",
            )
            all_details.append(long_detail)
            data_manifest["long_evaluation"] = long_manifest
            pass_long = (
                long_detail.outcome.implementation_status == "OK"
                and long_detail.outcome.daily_geometric_return >= float(spec["success_daily_geometric_return"])
                and long_detail.outcome.largest_profit_share is not None
                and long_detail.outcome.largest_profit_share <= float(config["gate"]["maximum_single_trade_profit_share"])
            )
            summary["long_evaluation"] = {
                "status": "PASS" if pass_long else "FAIL",
                "outcome": asdict(long_detail.outcome),
            }
            summary["status"] = "SUCCESS" if pass_long else "FAILED_LONG_EVALUATION"

    baseline_events = [
        {"run_id": detail.outcome.run_id, **event}
        for detail in baseline_details
        for event in detail.events
    ]
    baseline_trades = [
        {"run_id": detail.outcome.run_id, **trade}
        for detail in baseline_details
        for trade in detail.trades
    ]
    baseline_fills = [
        {"run_id": detail.outcome.run_id, **fill}
        for detail in baseline_details
        for fill in detail.fills
    ]
    write_json(output / "summary.json", summary)
    write_manifest(output / "data_manifest.json", data_manifest)
    write_jsonl(output / "events.jsonl", baseline_events)
    write_csv(output / "trades.csv", baseline_trades)
    write_csv(output / "fills.csv", baseline_fills)
    write_csv(output / "outcomes.csv", [asdict(detail.outcome) for detail in all_details])
    if args.mode == "gate":
        (output / "REPORT.md").write_text(
            report_markdown(summary, [detail.outcome for detail in baseline_details]),
            encoding="utf-8",
        )
    else:
        (output / "REPORT.md").write_text(
            "# Candidate 09 long evaluation\n\n"
            + f"Status: **{summary['status']}**\n\n"
            + json.dumps(json_safe(summary), indent=2)
            + "\n",
            encoding="utf-8",
        )
    run_manifest = {
        "candidate": config["candidate"],
        "mode": args.mode,
        "started_utc": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.time() - started,
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_branch": git_value("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(git_value("status", "--porcelain")),
        "python": sys.version,
        "platform": platform.platform(),
        "nautilus_trader": getattr(nautilus_trader, "__version__", "unknown"),
        "config_path": str(args.config.resolve()),
        "config_sha256": sha256_file(args.config),
        "data_manifest_sha256": sha256_file(output / "data_manifest.json"),
        "command": " ".join(sys.argv),
        "status": summary["status"],
    }
    write_json(output / "run.json", run_manifest)
    print(json.dumps(json_safe(summary), indent=2, sort_keys=True))
    # A failed economic hypothesis is a valid research result.  Only implementation
    # contract failures should make CI red and demand an identical-week rerun.
    implementation_failures = [
        detail.outcome.run_id
        for detail in all_details
        if detail.outcome.implementation_status != "OK"
    ]
    if implementation_failures:
        print(f"implementation failures: {implementation_failures}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
