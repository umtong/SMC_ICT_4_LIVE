"""Pure evaluation contracts for the candidate-08 shared-account acceptance system.

This module contains no NautilusTrader imports. It turns completed execution evidence into explicit
causality, risk-budget, concentration, and promotion decisions so the same rules can be unit tested
without modifying the trading scenario.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from math import exp, log
from typing import Any, Mapping, Sequence


def _float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    return float(value)


def fill_and_risk_contract_checks(
    intents: Sequence[Mapping[str, Any]],
    closed_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    early_fills: list[dict[str, Any]] = []
    planned_excess: list[dict[str, Any]] = []
    fill_adjusted_excess: list[dict[str, Any]] = []
    missing_fill_times: list[str] = []
    missing_fill_prices: list[str] = []
    missing_funding_states: list[str] = []
    future_funding_states: list[dict[str, Any]] = []
    invalid_funding_reserves: list[dict[str, Any]] = []
    maximum_fill_adjusted_ratio = 0.0

    intent_by_scenario: dict[str, Mapping[str, Any]] = {}
    for intent in intents:
        scenario_id = str(intent.get("scenario_id"))
        intent_by_scenario[scenario_id] = intent
        fill_time = intent.get("entry_fill_time_ns")
        fill_price = intent.get("entry_fill_price")
        if fill_time is None:
            missing_fill_times.append(scenario_id)
        elif int(fill_time) < int(intent["signal_time_ns"]):
            early_fills.append(
                {
                    "scenario_id": scenario_id,
                    "signal_time_ns": int(intent["signal_time_ns"]),
                    "entry_fill_time_ns": int(fill_time),
                }
            )
        if fill_price is None:
            missing_fill_prices.append(scenario_id)

        funding_fields = (
            "funding_observed_time_ns",
            "funding_rate_observed",
            "funding_interval_minutes",
            "expected_funding_crossings",
            "expected_funding_reserve_per_unit",
        )
        if any(intent.get(name) is None for name in funding_fields):
            missing_funding_states.append(scenario_id)
        else:
            observed_time = int(intent["funding_observed_time_ns"])
            signal_time = int(intent["signal_time_ns"])
            if observed_time > signal_time:
                future_funding_states.append(
                    {
                        "scenario_id": scenario_id,
                        "funding_observed_time_ns": observed_time,
                        "signal_time_ns": signal_time,
                    }
                )
            interval = int(intent["funding_interval_minutes"])
            crossings = int(intent["expected_funding_crossings"])
            reserve = float(intent["expected_funding_reserve_per_unit"])
            if interval <= 0 or crossings < 0 or reserve < 0:
                invalid_funding_reserves.append(
                    {
                        "scenario_id": scenario_id,
                        "funding_interval_minutes": interval,
                        "expected_funding_crossings": crossings,
                        "expected_funding_reserve_per_unit": reserve,
                    }
                )

        budget = _float(intent.get("risk_budget"))
        planned = _float(intent.get("planned_stop_loss"))
        tolerance = max(1e-8, budget * 1e-9)
        if planned > budget + tolerance:
            planned_excess.append(
                {
                    "scenario_id": scenario_id,
                    "planned_stop_loss": planned,
                    "risk_budget": budget,
                    "ratio": planned / budget if budget > 0 else None,
                }
            )

        fill_adjusted = intent.get("fill_adjusted_expected_stop_loss")
        if fill_adjusted is not None:
            fill_adjusted_value = float(fill_adjusted)
            ratio = fill_adjusted_value / budget if budget > 0 else float("inf")
            maximum_fill_adjusted_ratio = max(maximum_fill_adjusted_ratio, ratio)
            if fill_adjusted_value > budget + tolerance:
                fill_adjusted_excess.append(
                    {
                        "scenario_id": scenario_id,
                        "fill_adjusted_expected_stop_loss": fill_adjusted_value,
                        "risk_budget": budget,
                        "ratio": ratio,
                        "entry_fill_price": fill_price,
                        "entry_reference": intent.get("entry_reference"),
                    }
                )

    realized_excess: list[dict[str, Any]] = []
    unmatched_closed: list[str] = []
    missing_close_times: list[str] = []
    nonpositive_holding_times: list[dict[str, Any]] = []
    maximum_realized_loss_ratio = 0.0
    for trade in closed_trades:
        scenario_id = str(trade.get("scenario_id"))
        intent = intent_by_scenario.get(scenario_id)
        if intent is None:
            unmatched_closed.append(scenario_id)
            continue
        close_time = trade.get("position_close_time_ns")
        entry_fill_time = trade.get("entry_fill_time_ns", intent.get("entry_fill_time_ns"))
        if close_time is None:
            missing_close_times.append(scenario_id)
        elif entry_fill_time is not None and int(close_time) <= int(entry_fill_time):
            nonpositive_holding_times.append(
                {
                    "scenario_id": scenario_id,
                    "entry_fill_time_ns": int(entry_fill_time),
                    "position_close_time_ns": int(close_time),
                    "close_reason": trade.get("close_reason"),
                }
            )
        pnl = _float(trade.get("realized_pnl"))
        if pnl >= 0:
            continue
        budget = _float(intent.get("risk_budget"))
        ratio = abs(pnl) / budget if budget > 0 else float("inf")
        maximum_realized_loss_ratio = max(maximum_realized_loss_ratio, ratio)
        tolerance = max(1e-8, budget * 1e-9)
        if abs(pnl) > budget + tolerance:
            realized_excess.append(
                {
                    "scenario_id": scenario_id,
                    "symbol": trade.get("symbol"),
                    "realized_pnl": pnl,
                    "risk_budget": budget,
                    "ratio": ratio,
                    "close_reason": trade.get("close_reason"),
                }
            )

    return {
        "entry_fill_before_signal_count": len(early_fills),
        "entry_fill_before_signal_details": early_fills,
        "planned_loss_over_budget_count": len(planned_excess),
        "planned_loss_over_budget_details": planned_excess,
        "fill_adjusted_loss_over_budget_count": len(fill_adjusted_excess),
        "fill_adjusted_loss_over_budget_details": fill_adjusted_excess,
        "maximum_fill_adjusted_risk_budget_ratio": maximum_fill_adjusted_ratio,
        "realized_loss_over_budget_count": len(realized_excess),
        "realized_loss_over_budget_details": realized_excess,
        "maximum_realized_loss_budget_ratio": maximum_realized_loss_ratio,
        "missing_entry_fill_time_count": len(missing_fill_times),
        "missing_entry_fill_time_scenarios": missing_fill_times,
        "missing_entry_fill_price_count": len(missing_fill_prices),
        "missing_entry_fill_price_scenarios": missing_fill_prices,
        "missing_funding_cost_state_count": len(missing_funding_states),
        "missing_funding_cost_state_scenarios": missing_funding_states,
        "funding_observation_after_signal_count": len(future_funding_states),
        "funding_observation_after_signal_details": future_funding_states,
        "invalid_funding_reserve_count": len(invalid_funding_reserves),
        "invalid_funding_reserve_details": invalid_funding_reserves,
        "unmatched_closed_trade_count": len(unmatched_closed),
        "unmatched_closed_trade_scenarios": unmatched_closed,
        "missing_position_close_time_count": len(missing_close_times),
        "missing_position_close_time_scenarios": missing_close_times,
        "nonpositive_position_holding_time_count": len(nonpositive_holding_times),
        "nonpositive_position_holding_time_details": nonpositive_holding_times,
    }


def first_window_gate(
    config: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> dict[str, bool]:
    gate = config["first_window_gate"]
    checks = metrics["contract_checks"]
    return {
        "minimum_closed_trades": int(metrics["position_metrics"]["closed_trades"])
        >= int(gate["minimum_closed_trades"]),
        "cost_after_total_return_positive": float(metrics["total_return"]) > 0,
        "no_execution_failures": int(metrics["execution_failures"]) == 0,
        "no_residual_exposure": (
            int(metrics["open_positions_after_run"]) == 0
            and int(metrics["open_orders_after_run"]) == 0
        ),
        "no_unexpected_or_liquidation_closes": int(
            metrics["unexpected_or_liquidation_closes"]
        )
        == 0,
        "entry_causality": int(checks["entry_fill_before_signal_count"]) == 0,
        "planned_risk_budget_respected": int(checks["planned_loss_over_budget_count"]) == 0,
        "fill_adjusted_risk_budget_respected": int(
            checks["fill_adjusted_loss_over_budget_count"]
        )
        == 0,
        "realized_loss_budget_respected": int(checks["realized_loss_over_budget_count"]) == 0,
        "all_submitted_entries_observed": (
            int(checks["missing_entry_fill_time_count"]) == 0
            and int(checks["missing_entry_fill_price_count"]) == 0
        ),
        "funding_cost_state_is_causal_and_complete": (
            int(checks["missing_funding_cost_state_count"]) == 0
            and int(checks["funding_observation_after_signal_count"]) == 0
            and int(checks["invalid_funding_reserve_count"]) == 0
        ),
        "closed_trades_matched_to_intents": int(checks["unmatched_closed_trade_count"]) == 0,
        "position_exit_causality": (
            int(checks["missing_position_close_time_count"]) == 0
            and int(checks["nonpositive_position_holding_time_count"]) == 0
        ),
        "all_signal_times_processed": int(metrics["unprocessed_signal_times"]) == 0,
    }


def _single_positive_share(pnls: Sequence[float]) -> float:
    positive = [value for value in pnls if value > 0]
    total = sum(positive)
    return max(positive) / total if positive and total > 0 else 0.0


def suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    total_days = sum(float(item["calendar_days"]) for item in results)
    multiples = [float(item["nav_multiple"]) for item in results]
    if any(value <= 0 for value in multiples):
        combined_daily = -1.0
    else:
        combined_daily = exp(sum(log(value) for value in multiples) / total_days) - 1.0 if total_days else 0.0

    closed_trades = [
        trade
        for result in results
        for trade in result.get("closed_trade_records", [])
    ]
    pnls = [float(item["realized_pnl"]) for item in closed_trades]
    wins = sum(value > 0 for value in pnls)
    positive_trade_share = wins / len(pnls) if pnls else 0.0
    by_symbol_pnl: dict[str, float] = defaultdict(float)
    for trade in closed_trades:
        by_symbol_pnl[str(trade.get("symbol"))] += float(trade["realized_pnl"])
    positive_by_symbol = {symbol: pnl for symbol, pnl in by_symbol_pnl.items() if pnl > 0}
    total_positive_by_symbol = sum(positive_by_symbol.values())
    single_asset_positive_share = (
        max(positive_by_symbol.values()) / total_positive_by_symbol
        if positive_by_symbol and total_positive_by_symbol > 0
        else 0.0
    )

    ablation_modes = sorted({str(item.get("ablation", "none")) for item in results})
    promotable = ablation_modes == ["none"]
    summary: dict[str, Any] = {
        "candidate": config["candidate"],
        "suite": suite,
        "ablation_modes": ablation_modes,
        "promotable": promotable,
        "windows": [item["window"] for item in results],
        "window_results": [
            {
                "name": item["window"]["name"],
                "closed_trades": item["position_metrics"]["closed_trades"],
                "wins": item["position_metrics"]["wins"],
                "win_rate": item["position_metrics"]["win_rate"],
                "final_nav_usdt": item["final_nav_usdt"],
                "total_return": item["total_return"],
                "daily_geometric_growth": item["daily_geometric_growth"],
                "maximum_realized_equity_drawdown": item["maximum_realized_equity_drawdown"],
                "first_window_gate_passed": item["first_window_gate_passed"],
            }
            for item in results
        ],
        "combined_calendar_days": total_days,
        "combined_daily_geometric_growth": combined_daily,
        "goal_daily_geometric_growth": 0.01,
        "goal_met": combined_daily >= 0.01,
        "closed_trades": len(pnls),
        "wins": wins,
        "positive_trade_share": positive_trade_share,
        "single_trade_positive_pnl_share": _single_positive_share(pnls),
        "realized_pnl_by_symbol": dict(sorted(by_symbol_pnl.items())),
        "symbols_with_closed_trades": sorted(
            {str(item.get("symbol")) for item in closed_trades if item.get("symbol")}
        ),
        "single_asset_positive_pnl_share": single_asset_positive_share,
        "close_reasons": dict(sorted(Counter(str(item.get("close_reason")) for item in closed_trades).items())),
    }

    if suite == "first":
        first_checks = dict(results[0]["first_window_gate_checks"]) if results else {}
        first_checks["base_contract_not_ablated"] = promotable
        summary["suite_gate_checks"] = first_checks
        summary["suite_gate_passed"] = bool(
            results and results[0]["first_window_gate_passed"] and promotable
        )
        return summary

    if suite == "screen":
        gate = config["screen_gate"]
        contract_keys = (
            "entry_fill_before_signal_count",
            "planned_loss_over_budget_count",
            "fill_adjusted_loss_over_budget_count",
            "realized_loss_over_budget_count",
            "missing_entry_fill_time_count",
            "missing_entry_fill_price_count",
            "missing_funding_cost_state_count",
            "funding_observation_after_signal_count",
            "invalid_funding_reserve_count",
            "unmatched_closed_trade_count",
            "missing_position_close_time_count",
            "nonpositive_position_holding_time_count",
        )
        expected_windows = [dict(item) for item in config["suites"]["screen"]]
        observed_windows = [dict(item["window"]) for item in results]
        checks = {
            "base_contract_not_ablated": promotable,
            "exactly_three_predeclared_windows": (
                len(results) == 3 and observed_windows == expected_windows
            ),
            "minimum_closed_trades_each_week": all(
                int(item["position_metrics"]["closed_trades"])
                >= int(gate["minimum_closed_trades_per_week"])
                for item in results
            ),
            "all_three_cost_after_positive": all(float(item["total_return"]) > 0 for item in results),
            "minimum_positive_trade_share": positive_trade_share
            >= float(gate["minimum_positive_trade_share"]),
            "maximum_single_positive_pnl_share": summary["single_trade_positive_pnl_share"]
            <= float(gate["maximum_single_positive_pnl_share"]),
            "combined_daily_geometric_growth": combined_daily
            >= float(gate["combined_daily_geometric_growth"]),
            "no_execution_failures": all(int(item["execution_failures"]) == 0 for item in results),
            "no_residual_exposure": all(
                int(item["open_positions_after_run"]) == 0
                and int(item["open_orders_after_run"]) == 0
                for item in results
            ),
            "no_unexpected_or_liquidation_closes": all(
                int(item["unexpected_or_liquidation_closes"]) == 0 for item in results
            ),
            "execution_contracts_hold": all(
                int(item["contract_checks"][key]) == 0
                for item in results
                for key in contract_keys
            )
            and all(int(item["unprocessed_signal_times"]) == 0 for item in results),
        }
        summary["suite_gate_checks"] = checks
        summary["suite_gate_passed"] = all(checks.values())
    return summary
