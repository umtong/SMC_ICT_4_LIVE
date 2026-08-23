"""Compact, threshold-free evidence for liquidity-episode research.

This module is intentionally an evidence exporter, not a promotion gate.  It
accepts the native objects used by :mod:`episode_policy_live`, normalized
dictionaries, pandas-like NautilusTrader reports, or position/event objects.
Missing report fields stay missing; they are never converted into a pass/fail.

Episode labels are split into information available at decision time and an
offline maturity label.  A non-trade cannot be called structurally missed until
its declared maturity time has passed in the supplied evidence clock.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
import csv
import heapq
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


DAY_NS = 86_400_000_000_000
MINUTE_NS = 60_000_000_000


TRADE_ALIASES: dict[str, tuple[str, ...]] = {
    "trade_id": ("trade_id", "plan_id", "position_id", "id", "client_order_id"),
    "episode_id": ("episode_id", "causal_episode_id", "episode"),
    "symbol": ("symbol", "instrument_id", "instrument", "instrument.id"),
    "family": ("family", "strategy_family", "event_family"),
    "side": ("side", "entry_side", "position_side"),
    "entry_time_ns": ("entry_time_ns", "ts_opened", "open_time_ns", "opened_at", "entry_time"),
    "exit_time_ns": ("exit_time_ns", "ts_closed", "close_time_ns", "closed_at", "exit_time"),
    "entry_price": ("entry_price", "avg_px_open", "avg_open_price", "price_open"),
    "exit_price": ("exit_price", "avg_px_close", "avg_close_price", "price_close"),
    "quantity": ("quantity", "qty", "peak_qty", "size"),
    "gross_pnl": ("gross_pnl", "realized_gross_pnl", "pnl_gross"),
    "fees": ("fees", "commissions", "commission", "total_fees"),
    "slippage_cost": ("slippage_cost", "slippage"),
    "funding": ("funding", "funding_paid", "funding_cost"),
    "net_pnl": ("net_pnl", "realized_pnl", "pnl", "realized_return"),
    "gross_r": ("gross_r", "realized_gross_r"),
    "net_r": ("net_r", "realized_net_r", "account_r"),
    "planned_gross_rr": ("planned_gross_rr", "gross_rr", "planned_rr", "reward_risk"),
    "risk_cash": ("risk_cash", "planned_risk_cash", "initial_risk"),
    "stop_price": ("stop_price", "stop", "invalidation_price"),
    "outcome": ("outcome", "exit_reason", "result"),
    "nav_before": ("nav_before", "balance_before", "equity_before"),
    "nav_after": ("nav_after", "balance_after", "equity_after"),
}

EPISODE_ALIASES: dict[str, tuple[str, ...]] = {
    "episode_id": ("episode_id", "causal_episode_id", "id"),
    "symbol": ("symbol", "instrument_id", "instrument"),
    "family": ("family", "event_family", "strategy_family"),
    "side": ("side", "direction"),
    "decision_time_ns": ("decision_time_ns", "observed_time_ns", "emission_time_ns", "event_time_ns"),
    "maturity_time_ns": ("maturity_time_ns", "label_available_time_ns", "expires_time_ns", "horizon_time_ns"),
    "as_of_time_ns": ("as_of_time_ns", "evaluation_time_ns", "observed_through_time_ns"),
    "decision_disposition": ("decision_disposition", "disposition", "status", "action"),
    "abstain_reason": ("abstain_reason", "reason", "rejection_reason", "decision_reason"),
    "entry": ("entry", "entry_price"),
    "stop": ("stop", "stop_price", "invalidation_price"),
    "target": ("target", "target_price", "objective_price"),
    "order_type": ("order_type", "entry_type"),
    "counterfactual_outcome": (
        "counterfactual_outcome",
        "matured_outcome",
        "outcome_after_maturity",
        "would_have_outcome",
    ),
    "counterfactual_net_r": ("counterfactual_net_r", "matured_net_r", "would_have_net_r"),
}

EQUITY_ALIASES: dict[str, tuple[str, ...]] = {
    "time_ns": ("time_ns", "ts_event", "timestamp", "ts", "date"),
    "equity": ("equity", "nav", "account_value", "total_balance", "balance"),
}

BAR_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "instrument_id", "instrument"),
    "open_time_ns": ("open_time_ns", "ts_init", "time_ns", "timestamp"),
    "close_time_ns": ("close_time_ns", "ts_event", "time_ns", "timestamp"),
    "open": ("open",),
    "high": ("high",),
    "low": ("low",),
    "close": ("close",),
    "volume": ("volume",),
}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(math.isnan(value))
    except (TypeError, ValueError):
        return False


def _number(value: Any) -> float | None:
    if _is_missing(value):
        return None
    if hasattr(value, "as_double"):
        try:
            value = value.as_double()
        except (TypeError, ValueError):
            pass
    if isinstance(value, str):
        match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value.replace(",", ""))
        if match is None:
            return None
        value = match.group(0)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _time_ns(value: Any) -> int | None:
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        item = value if value.tzinfo else value.replace(tzinfo=UTC)
        return int(item.timestamp() * 1_000_000_000)
    if hasattr(value, "value"):
        possible = getattr(value, "value")
        if isinstance(possible, int):
            return possible
    if isinstance(value, str) and not re.fullmatch(r"[-+]?\d+", value.strip()):
        try:
            item = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if item.tzinfo is None:
                item = item.replace(tzinfo=UTC)
            return int(item.timestamp() * 1_000_000_000)
        except ValueError:
            return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    # Integer timestamps follow Nautilus' nanosecond contract.  Do not guess a
    # unit from magnitude: deterministic tests and bounded replays legitimately
    # use small ns values relative to an epoch of zero.
    return number


def _record(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        try:
            converted = converter()
            if isinstance(converted, Mapping):
                return dict(converted)
        except TypeError:
            pass
    result: dict[str, Any] = {}
    values = getattr(value, "__dict__", None)
    if isinstance(values, Mapping):
        result.update(values)
    for aliases in (*TRADE_ALIASES.values(), *EPISODE_ALIASES.values(), *EQUITY_ALIASES.values(), *BAR_ALIASES.values()):
        for name in aliases:
            if "." not in name and name not in result and hasattr(value, name):
                try:
                    result[name] = getattr(value, name)
                except Exception:  # pragma: no cover - opaque native descriptor
                    pass
    return result


def _records(source: Any) -> list[dict[str, Any]]:
    if source is None:
        return []
    # pandas/polars-like report without importing either dependency.
    converter = getattr(source, "to_dict", None)
    if callable(converter) and not isinstance(source, Mapping):
        try:
            converted = converter(orient="records")
            if isinstance(converted, list):
                return [_record(item) for item in converted]
        except TypeError:
            pass
    if isinstance(source, Mapping) or is_dataclass(source):
        return [_record(source)]
    if isinstance(source, (str, bytes)):
        raise TypeError("evidence source must contain records, not text")
    try:
        return [_record(item) for item in source]
    except TypeError:
        return [_record(source)]


def _lookup(record: Mapping[str, Any], aliases: Sequence[str]) -> Any:
    for name in aliases:
        if name in record and not _is_missing(record[name]):
            return record[name]
        if "." in name:
            value: Any = record
            for part in name.split("."):
                if isinstance(value, Mapping) and part in value:
                    value = value[part]
                else:
                    value = None
                    break
            if not _is_missing(value):
                return value
    # Common native event layout: strategy metadata lives in info/details.
    for container in ("details", "metadata", "info", "payload", "tags"):
        nested = record.get(container)
        if isinstance(nested, Mapping):
            value = _lookup(nested, aliases)
            if not _is_missing(value):
                return value
    return None


def _text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    return str(value)


def _symbol(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    # Keep normalized symbols stable across BINANCE position report identifiers
    # such as BTCUSDT-PERP.BINANCE and BTCUSDT.BINANCE.
    return re.split(r"[-.]", text, maxsplit=1)[0].upper()


def normalize_trade_records(source: Any, *, risk_fraction: float = 0.03) -> list[dict[str, Any]]:
    """Normalize strategy trades or Nautilus position/event report rows.

    No required performance field is invented.  ``gross_r`` can be derived from
    explicit risk cash, stop geometry, or (for this fixed-risk account) NAV before
    the trade.  The result includes ``available_fields`` for auditability.
    """

    output: list[dict[str, Any]] = []
    for index, raw in enumerate(_records(source)):
        item: dict[str, Any] = {}
        for key, aliases in TRADE_ALIASES.items():
            value = _lookup(raw, aliases)
            if key in {"entry_time_ns", "exit_time_ns"}:
                item[key] = _time_ns(value)
            elif key in {
                "entry_price", "exit_price", "quantity", "gross_pnl", "fees",
                "slippage_cost", "funding", "net_pnl", "gross_r", "net_r",
                "planned_gross_rr", "risk_cash", "stop_price", "nav_before", "nav_after",
            }:
                item[key] = _number(value)
            elif key == "symbol":
                item[key] = _symbol(value)
            else:
                item[key] = _text(value)

        if item["trade_id"] is None:
            item["trade_id"] = f"trade:{index}"
        if item["net_pnl"] is None and item["gross_pnl"] is not None:
            costs = sum(item[name] or 0.0 for name in ("fees", "slippage_cost", "funding"))
            item["net_pnl"] = item["gross_pnl"] - costs
        if item["gross_pnl"] is None and item["net_pnl"] is not None:
            known_costs = [item[name] for name in ("fees", "slippage_cost", "funding")]
            if any(value is not None for value in known_costs):
                item["gross_pnl"] = item["net_pnl"] + sum(value or 0.0 for value in known_costs)

        risk_cash = item["risk_cash"]
        if risk_cash is None and all(item[name] is not None for name in ("entry_price", "stop_price", "quantity")):
            risk_cash = abs(item["entry_price"] - item["stop_price"]) * abs(item["quantity"])
        if risk_cash is None and item["nav_before"] is not None and risk_fraction > 0.0:
            risk_cash = item["nav_before"] * risk_fraction
        item["risk_cash"] = risk_cash
        if item["gross_r"] is None and item["gross_pnl"] is not None and risk_cash is not None and risk_cash > 0.0:
            item["gross_r"] = item["gross_pnl"] / risk_cash
        if item["net_r"] is None and item["net_pnl"] is not None and risk_cash is not None and risk_cash > 0.0:
            item["net_r"] = item["net_pnl"] / risk_cash
        if item["outcome"] is None and item["net_pnl"] is not None:
            item["outcome"] = "WIN" if item["net_pnl"] > 0.0 else "LOSS" if item["net_pnl"] < 0.0 else "FLAT"
        item["available_fields"] = sorted(key for key, value in item.items() if value is not None)
        output.append(item)
    return output


def normalize_equity_records(source: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in _records(source):
        time_ns = _time_ns(_lookup(raw, EQUITY_ALIASES["time_ns"]))
        equity = _number(_lookup(raw, EQUITY_ALIASES["equity"]))
        if time_ns is not None and equity is not None:
            output.append({"time_ns": time_ns, "equity": equity})
    return sorted(output, key=lambda item: item["time_ns"])


def normalize_bar_records(source: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in _records(source):
        item = {
            "symbol": _symbol(_lookup(raw, BAR_ALIASES["symbol"])),
            "open_time_ns": _time_ns(_lookup(raw, BAR_ALIASES["open_time_ns"])),
            "close_time_ns": _time_ns(_lookup(raw, BAR_ALIASES["close_time_ns"])),
            "open": _number(_lookup(raw, BAR_ALIASES["open"])),
            "high": _number(_lookup(raw, BAR_ALIASES["high"])),
            "low": _number(_lookup(raw, BAR_ALIASES["low"])),
            "close": _number(_lookup(raw, BAR_ALIASES["close"])),
            "volume": _number(_lookup(raw, BAR_ALIASES["volume"])),
        }
        if item["close_time_ns"] is None:
            item["close_time_ns"] = item["open_time_ns"]
        if item["open_time_ns"] is None:
            item["open_time_ns"] = item["close_time_ns"]
        if item["symbol"] and item["close_time_ns"] is not None:
            output.append(item)
    return sorted(output, key=lambda item: (item["close_time_ns"], item["symbol"]))


def normalize_episode_records(source: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, raw in enumerate(_records(source)):
        item: dict[str, Any] = {}
        for key, aliases in EPISODE_ALIASES.items():
            value = _lookup(raw, aliases)
            if key.endswith("_time_ns"):
                item[key] = _time_ns(value)
            elif key in {"entry", "stop", "target", "counterfactual_net_r"}:
                item[key] = _number(value)
            elif key == "symbol":
                item[key] = _symbol(value)
            else:
                item[key] = _text(value)
        if item["episode_id"] is None:
            item["episode_id"] = f"episode:{index}"
        output.append(item)
    return output


def _mean(values: Iterable[float | None]) -> float | None:
    usable = [value for value in values if value is not None and math.isfinite(value)]
    return sum(usable) / len(usable) if usable else None


def _continuous_drawdown(equity: Sequence[Mapping[str, Any]]) -> tuple[float | None, int]:
    peak: float | None = None
    maximum = 0.0
    count = 0
    for point in equity:
        value = _number(point.get("equity"))
        if value is None:
            continue
        count += 1
        peak = value if peak is None else max(peak, value)
        if peak > 0.0:
            maximum = max(maximum, 1.0 - value / peak)
    return (maximum if count else None), count


def _overlap_pairs(trades: Sequence[Mapping[str, Any]]) -> int:
    intervals = sorted(
        (int(item["entry_time_ns"]), int(item["exit_time_ns"]))
        for item in trades
        if item.get("entry_time_ns") is not None
        and item.get("exit_time_ns") is not None
        and int(item["exit_time_ns"]) >= int(item["entry_time_ns"])
    )
    active: list[int] = []
    pairs = 0
    for start, end in intervals:
        while active and active[0] <= start:
            heapq.heappop(active)
        pairs += len(active)
        heapq.heappush(active, end)
    return pairs


def trade_ledger_metrics(
    trades: Any,
    *,
    equity: Any = None,
    start_time_ns: int | datetime | str | None = None,
    end_time_ns: int | datetime | str | None = None,
    initial_nav: float | None = None,
    final_nav: float | None = None,
    expected_symbols: Iterable[str] | None = None,
    risk_fraction: float = 0.03,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Return normalized ledger, descriptive metrics, and the equity evidence.

    There are deliberately no target values, booleans named ``pass``, or
    promotion decisions in the returned metrics.
    """

    ledger = normalize_trade_records(trades, risk_fraction=risk_fraction)
    equity_points = normalize_equity_records(equity)
    if not equity_points:
        for trade in sorted(ledger, key=lambda item: item.get("exit_time_ns") or 0):
            if trade.get("entry_time_ns") is not None and trade.get("nav_before") is not None:
                equity_points.append({"time_ns": trade["entry_time_ns"], "equity": trade["nav_before"]})
            if trade.get("exit_time_ns") is not None and trade.get("nav_after") is not None:
                equity_points.append({"time_ns": trade["exit_time_ns"], "equity": trade["nav_after"]})
        equity_points.sort(key=lambda item: item["time_ns"])

    start = _time_ns(start_time_ns)
    end = _time_ns(end_time_ns)
    observed_times = [
        int(item[name])
        for item in ledger
        for name in ("entry_time_ns", "exit_time_ns")
        if item.get(name) is not None
    ] + [int(item["time_ns"]) for item in equity_points]
    if start is None and observed_times:
        start = min(observed_times)
    if end is None and observed_times:
        end = max(observed_times)
    days = (end - start) / DAY_NS if start is not None and end is not None and end > start else None

    net_pnls = [item["net_pnl"] for item in ledger if item.get("net_pnl") is not None]
    wins = sum(value > 0.0 for value in net_pnls)
    gross_profit = sum(max(value, 0.0) for value in net_pnls)
    gross_loss = -sum(min(value, 0.0) for value in net_pnls)
    drawdown, drawdown_observations = _continuous_drawdown(equity_points)

    nav_start = _number(initial_nav)
    nav_end = _number(final_nav)
    if nav_start is None:
        nav_start = equity_points[0]["equity"] if equity_points else next(
            (item["nav_before"] for item in ledger if item.get("nav_before") is not None), None
        )
    if nav_end is None:
        nav_end = equity_points[-1]["equity"] if equity_points else next(
            (item["nav_after"] for item in reversed(ledger) if item.get("nav_after") is not None), None
        )

    traded_symbols = sorted({item["symbol"] for item in ledger if item.get("symbol")})
    universe = sorted({_symbol(value) for value in (expected_symbols or ()) if _symbol(value)})
    episodes = [item["episode_id"] for item in ledger if item.get("episode_id")]
    duplicate_episode_ids = len(episodes) - len(set(episodes))
    overlap_pairs = _overlap_pairs(ledger)
    available_counts = {
        name: sum(item.get(name) is not None for item in ledger)
        for name in ("gross_r", "planned_gross_rr", "net_r", "net_pnl", "episode_id", "entry_time_ns", "exit_time_ns")
    }
    metrics = {
        "start_time_ns": start,
        "end_time_ns": end,
        "calendar_days": days,
        "completed_trades": len(ledger),
        "trades_per_calendar_day": len(ledger) / days if days and days > 0.0 else None,
        "win_rate": wins / len(net_pnls) if net_pnls else None,
        "average_gross_r": _mean(item.get("gross_r") for item in ledger),
        "average_planned_gross_rr": _mean(item.get("planned_gross_rr") for item in ledger),
        "average_net_r": _mean(item.get("net_r") for item in ledger),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "profit_factor_components": {"net_profit": gross_profit, "net_loss_abs": gross_loss},
        "initial_nav": nav_start,
        "final_nav": nav_end,
        "nav_change": nav_end - nav_start if nav_start is not None and nav_end is not None else None,
        "maximum_continuous_drawdown": drawdown,
        "drawdown_observations": drawdown_observations,
        "traded_symbols": traded_symbols,
        "expected_symbols": universe,
        "missing_symbol_coverage": sorted(set(universe) - set(traded_symbols)),
        "symbol_trade_counts": {symbol: sum(item.get("symbol") == symbol for item in ledger) for symbol in traded_symbols},
        "unique_episode_count": len(set(episodes)),
        "duplicate_episode_id_count": duplicate_episode_ids,
        "overlapping_trade_pair_count": overlap_pairs,
        "available_metric_fields": available_counts,
    }
    return ledger, metrics, equity_points


def _counterfactual_from_bars(episode: Mapping[str, Any], bars: Sequence[Mapping[str, Any]]) -> tuple[str | None, str]:
    required = (episode.get("side"), episode.get("entry"), episode.get("stop"), episode.get("target"))
    if any(value is None for value in required):
        return None, "NO_COUNTERFACTUAL_GEOMETRY"
    side = str(episode["side"]).upper()
    long_side = side in {"LONG", "BUY"}
    if not long_side and side not in {"SHORT", "SELL"}:
        return None, "UNKNOWN_SIDE"
    entry, stop, target = (float(episode[name]) for name in ("entry", "stop", "target"))
    decision = episode.get("decision_time_ns")
    maturity = episode.get("maturity_time_ns")
    symbol = episode.get("symbol")
    relevant = [
        bar for bar in bars
        if bar.get("symbol") == symbol
        and bar.get("close_time_ns") is not None
        and (decision is None or bar["close_time_ns"] > decision)
        and (maturity is None or bar["close_time_ns"] <= maturity)
    ]
    filled = str(episode.get("order_type") or "LIMIT").upper() == "MARKET"
    for bar in relevant:
        high, low = _number(bar.get("high")), _number(bar.get("low"))
        if high is None or low is None:
            continue
        if not filled:
            filled = low <= entry <= high
            if not filled:
                continue
        target_hit = high >= target if long_side else low <= target
        stop_hit = low <= stop if long_side else high >= stop
        if stop_hit:  # Conservative ordering when both occur in one bar.
            return "STOP_FIRST", "BAR_PATH_STOP_OR_AMBIGUOUS"
        if target_hit:
            return "TARGET_FIRST", "BAR_PATH_TARGET_FIRST"
    return ("UNRESOLVED" if filled else "NOT_FILLED"), "BAR_PATH_TO_MATURITY"


def _positive_counterfactual(outcome: str | None, net_r: float | None) -> bool:
    if net_r is not None:
        return net_r > 0.0
    if outcome is None:
        return False
    normalized = outcome.upper().replace("-", "_").replace(" ", "_")
    return normalized in {"TARGET", "TARGET_FIRST", "WIN", "WINNER", "PROFITABLE", "TAKE_PROFIT"}


def build_episode_ledger(
    episodes: Any,
    *,
    trades: Any = None,
    bars: Any = None,
    as_of_time_ns: int | datetime | str | None = None,
) -> list[dict[str, Any]]:
    """Label traded, structurally missed, and abstained episodes after maturity.

    ``counterfactual_*`` values and future bars are used only in maturity labels;
    they are not copied into ``decision_evidence``.  Episodes without a declared
    maturity or whose maturity is later than ``as_of`` remain explicitly pending.
    """

    normalized = normalize_episode_records(episodes)
    trade_rows = normalize_trade_records(trades)
    traded_ids = {item["episode_id"] for item in trade_rows if item.get("episode_id")}
    bar_rows = normalize_bar_records(bars)
    requested_as_of = _time_ns(as_of_time_ns)
    global_as_of = requested_as_of
    if global_as_of is None:
        candidates = [item.get("as_of_time_ns") for item in normalized if item.get("as_of_time_ns") is not None]
        candidates += [item["close_time_ns"] for item in bar_rows if item.get("close_time_ns") is not None]
        candidates += [item["exit_time_ns"] for item in trade_rows if item.get("exit_time_ns") is not None]
        global_as_of = max(candidates) if candidates else None

    output: list[dict[str, Any]] = []
    for item in normalized:
        episode_id = item["episode_id"]
        maturity = item.get("maturity_time_ns")
        # An explicit evidence clock is an upper bound even when a source row
        # was exported later.  This makes historical "what was labelable then?"
        # queries deterministic and prevents later row metadata leaking backward.
        local_as_of = requested_as_of if requested_as_of is not None else (item.get("as_of_time_ns") or global_as_of)
        traded = episode_id in traded_ids or str(item.get("decision_disposition") or "").upper() in {
            "TRADE", "TRADED", "ENTERED", "FILLED"
        }
        if traded:
            label = "TRADED"
            basis = "TRADE_LEDGER_OR_DECISION_DISPOSITION"
            label_time = item.get("decision_time_ns")
            matured_outcome = None
            counterfactual_net_r = None
        elif maturity is None:
            label = "UNLABELLED_NO_MATURITY"
            basis = "MATURITY_TIME_REQUIRED"
            label_time = None
            matured_outcome = None
            counterfactual_net_r = None
        elif local_as_of is None or local_as_of < maturity:
            label = "PENDING_MATURITY"
            basis = "EVIDENCE_CLOCK_BEFORE_MATURITY"
            label_time = None
            matured_outcome = None
            counterfactual_net_r = None
        else:
            matured_outcome = item.get("counterfactual_outcome")
            counterfactual_net_r = item.get("counterfactual_net_r")
            basis = "DECLARED_MATURITY_OUTCOME"
            if matured_outcome is None and counterfactual_net_r is None:
                matured_outcome, basis = _counterfactual_from_bars(item, bar_rows)
            label = "STRUCTURALLY_MISSED" if _positive_counterfactual(matured_outcome, counterfactual_net_r) else "ABSTAINED"
            label_time = maturity

        decision_evidence = {
            key: item.get(key)
            for key in (
                "family", "side", "decision_disposition", "abstain_reason",
                "entry", "stop", "target", "order_type",
            )
            if item.get(key) is not None
        }
        output.append(
            {
                "episode_id": episode_id,
                "symbol": item.get("symbol"),
                "decision_time_ns": item.get("decision_time_ns"),
                "maturity_time_ns": maturity,
                "evidence_as_of_time_ns": local_as_of,
                "decision_evidence": decision_evidence,
                "maturity_label": label,
                "label_available_time_ns": label_time,
                "label_basis": basis,
                "matured_outcome": matured_outcome,
                "counterfactual_net_r": counterfactual_net_r,
            }
        )
    return output


def build_chart_window_specs(
    trades: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
    *,
    before_minutes: int = 180,
    after_minutes: int = 180,
) -> list[dict[str, Any]]:
    """Create renderer-neutral chart windows for traded and non-traded cases."""

    if before_minutes < 0 or after_minutes < 0:
        raise ValueError("chart window minutes must be non-negative")
    output: list[dict[str, Any]] = []
    for item in trades:
        anchor = item.get("entry_time_ns")
        if anchor is None:
            continue
        overlays = {name: item.get(name) for name in ("entry_price", "stop_price", "exit_price") if item.get(name) is not None}
        output.append(
            {
                "window_id": f"trade:{item.get('trade_id')}",
                "case_kind": "TRADE",
                "case_id": item.get("trade_id"),
                "episode_id": item.get("episode_id"),
                "symbol": item.get("symbol"),
                "anchor_time_ns": anchor,
                "window_start_time_ns": anchor - before_minutes * MINUTE_NS,
                "window_end_time_ns": (item.get("exit_time_ns") or anchor) + after_minutes * MINUTE_NS,
                "overlays": overlays,
            }
        )
    traded_episode_ids = {item.get("episode_id") for item in trades if item.get("episode_id")}
    for item in episodes:
        if item.get("maturity_label") == "TRADED" and item.get("episode_id") in traded_episode_ids:
            continue
        anchor = item.get("decision_time_ns")
        if anchor is None:
            continue
        decision = item.get("decision_evidence") or {}
        overlays = {name: decision.get(name) for name in ("entry", "stop", "target") if decision.get(name) is not None}
        output.append(
            {
                "window_id": f"episode:{item.get('episode_id')}",
                "case_kind": item.get("maturity_label"),
                "case_id": item.get("episode_id"),
                "episode_id": item.get("episode_id"),
                "symbol": item.get("symbol"),
                "anchor_time_ns": anchor,
                "window_start_time_ns": anchor - before_minutes * MINUTE_NS,
                "window_end_time_ns": (item.get("maturity_time_ns") or anchor) + after_minutes * MINUTE_NS,
                "overlays": overlays,
            }
        )
    return sorted(output, key=lambda item: (item.get("anchor_time_ns") or 0, item["window_id"]))


def build_evidence(
    *,
    trades: Any,
    episodes: Any = None,
    equity: Any = None,
    bars: Any = None,
    start_time_ns: int | datetime | str | None = None,
    end_time_ns: int | datetime | str | None = None,
    as_of_time_ns: int | datetime | str | None = None,
    initial_nav: float | None = None,
    final_nav: float | None = None,
    expected_symbols: Iterable[str] | None = None,
    risk_fraction: float = 0.03,
    chart_before_minutes: int = 180,
    chart_after_minutes: int = 180,
) -> dict[str, Any]:
    """Build the complete compact evidence payload without policy thresholds."""

    trade_ledger, metrics, equity_ledger = trade_ledger_metrics(
        trades,
        equity=equity,
        start_time_ns=start_time_ns,
        end_time_ns=end_time_ns,
        initial_nav=initial_nav,
        final_nav=final_nav,
        expected_symbols=expected_symbols,
        risk_fraction=risk_fraction,
    )
    episode_ledger = build_episode_ledger(
        episodes,
        trades=trade_ledger,
        bars=bars,
        as_of_time_ns=as_of_time_ns if as_of_time_ns is not None else end_time_ns,
    )
    chart_windows = build_chart_window_specs(
        trade_ledger,
        episode_ledger,
        before_minutes=chart_before_minutes,
        after_minutes=chart_after_minutes,
    )
    return {
        "schema_version": 1,
        "metrics": metrics,
        "trade_ledger": trade_ledger,
        "episode_ledger": episode_ledger,
        "equity_ledger": equity_ledger,
        "chart_windows": chart_windows,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(_json_safe(value), sort_keys=True) if isinstance(value, (Mapping, list, tuple)) else value
                    for key, value in row.items()
                }
            )


def write_evidence(destination: str | Path, evidence: Mapping[str, Any] | None = None, **build_kwargs: Any) -> dict[str, Path]:
    """Write JSON plus portable CSV ledgers/chart-window specifications."""

    if evidence is None:
        evidence = build_evidence(**build_kwargs)
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "evidence": root / "evidence.json",
        "metrics": root / "trade_metrics.json",
        "trade_ledger_json": root / "trade_ledger.json",
        "trade_ledger_csv": root / "trade_ledger.csv",
        "episode_ledger_json": root / "episode_ledger.json",
        "episode_ledger_csv": root / "episode_ledger.csv",
        "chart_windows_json": root / "chart_windows.json",
        "chart_windows_csv": root / "chart_windows.csv",
    }
    _write_json(paths["evidence"], evidence)
    _write_json(paths["metrics"], evidence.get("metrics", {}))
    for name in ("trade_ledger", "episode_ledger", "chart_windows"):
        rows = list(evidence.get(name, []))
        _write_json(paths[f"{name}_json"], rows)
        _write_csv(paths[f"{name}_csv"], rows)
    return paths


__all__ = [
    "build_chart_window_specs",
    "build_episode_ledger",
    "build_evidence",
    "normalize_bar_records",
    "normalize_episode_records",
    "normalize_equity_records",
    "normalize_trade_records",
    "trade_ledger_metrics",
    "write_evidence",
]
