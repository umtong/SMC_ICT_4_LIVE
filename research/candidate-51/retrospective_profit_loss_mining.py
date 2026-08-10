from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence"
OUT_JSON = EVIDENCE / "RETROSPECTIVE_PROFIT_LOSS_MINING_2026-08-10.json"
OUT_MD = EVIDENCE / "RETROSPECTIVE_PROFIT_LOSS_MINING_2026-08-10.md"

_PNL_RE = re.compile(r"[-+]?\d+(?:[,_]\d{3})*(?:\.\d+)?")


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else default
    match = _PNL_RE.search(str(value).replace("_", ""))
    if not match:
        return default
    try:
        result = float(match.group().replace(",", ""))
    except ValueError:
        return default
    return result if math.isfinite(result) else default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    if len(ordered) == 1:
        return ordered[0]
    index = q * (len(ordered) - 1)
    low = int(math.floor(index))
    high = int(math.ceil(index))
    if low == high:
        return ordered[low]
    weight = index - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0.0 else 0.0


def _scenario_pnl(item: dict[str, Any]) -> float:
    for key in ("realized_pnl_usdt", "realized_pnl", "pnl_usdt", "pnl"):
        if key in item:
            return _number(item.get(key))
    event = str(item.get("event") or "")
    match = re.search(r"realized_pnl=([-+0-9_.,]+)\s+USDT", event)
    return _number(match.group(1)) if match else 0.0


def _is_economically_valid(item: dict[str, Any]) -> bool:
    # Missing validity markers mean the historical adapter did not expose the
    # contract. Explicit false markers are implementation-contaminated and are
    # never used to judge strategy logic.
    markers = [
        item.get("actual_fill_risk_valid"),
        item.get("fill_risk_valid"),
        item.get("economic_valid"),
    ]
    explicit = [value for value in markers if value is not None]
    return all(bool(value) for value in explicit) if explicit else True


def _numeric_features(item: dict[str, Any]) -> dict[str, float]:
    features: dict[str, float] = {}
    diagnostics = item.get("diagnostics")
    if isinstance(diagnostics, dict):
        for key, value in diagnostics.items():
            if isinstance(value, bool):
                features[f"diag.{key}"] = float(value)
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                features[f"diag.{key}"] = float(value)
    for key in ("score", "side", "entry_reference", "stop", "target", "planned_account_loss", "risk_budget"):
        value = item.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            features[key] = float(value)
    ts = _int(item.get("episode_ts") or item.get("closed_ts_event"))
    if ts > 0:
        # Nanosecond timestamps in this project.
        hour = int((ts // 1_000_000_000 // 3600) % 24)
        features["episode_hour_utc"] = float(hour)
    return features


def _categorical_features(item: dict[str, Any]) -> dict[str, str]:
    result = {
        "symbol": str(item.get("symbol") or "UNKNOWN"),
        "side": str(item.get("side") or "UNKNOWN"),
        "state": str(item.get("state") or "UNKNOWN"),
    }
    diagnostics = item.get("diagnostics")
    if isinstance(diagnostics, dict):
        for key in (
            "source_tag", "regime", "market_state", "entry_tag", "family",
            "mode", "signal", "direction", "scenario_family",
        ):
            value = diagnostics.get(key)
            if value is not None and isinstance(value, (str, int, float, bool)):
                result[f"diag.{key}"] = str(value)
    return result


@dataclass(slots=True)
class Trade:
    pnl: float
    ts: int
    numeric: dict[str, float]
    categorical: dict[str, str]
    source: str


def _trade_summary(trades: list[Trade], days: int, starting_nav: float) -> dict[str, Any]:
    pnls = [trade.pnl for trade in trades]
    winners = [value for value in pnls if value > 0.0]
    losers = [value for value in pnls if value < 0.0]
    gross_profit = sum(winners)
    gross_loss = -sum(losers)
    net = sum(pnls)
    return {
        "trades": len(trades),
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": _safe_ratio(len(winners), len(trades)),
        "gross_profit_usdt": gross_profit,
        "gross_loss_usdt": gross_loss,
        "net_pnl_usdt": net,
        "profit_factor": _safe_ratio(gross_profit, gross_loss),
        "avg_winner_usdt": statistics.fmean(winners) if winners else 0.0,
        "avg_loser_usdt": statistics.fmean(losers) if losers else 0.0,
        "expectancy_usdt": statistics.fmean(pnls) if pnls else 0.0,
        "trade_density_per_day": _safe_ratio(len(trades), days),
        "gross_profit_capacity_per_day_nav": _safe_ratio(gross_profit, starting_nav * days),
        "gross_loss_burden_per_day_nav": _safe_ratio(gross_loss, starting_nav * days),
        "net_pnl_per_day_nav": _safe_ratio(net, starting_nav * days),
        "gross_profit_to_net_abs": _safe_ratio(gross_profit, abs(net)) if net else math.inf,
    }


def _evaluate_keep(trades: list[Trade], keep: Iterable[bool]) -> dict[str, Any]:
    selected = [trade for trade, flag in zip(trades, keep) if flag]
    base_profit = sum(max(trade.pnl, 0.0) for trade in trades)
    base_loss = -sum(min(trade.pnl, 0.0) for trade in trades)
    kept_profit = sum(max(trade.pnl, 0.0) for trade in selected)
    kept_loss = -sum(min(trade.pnl, 0.0) for trade in selected)
    return {
        "kept_trades": len(selected),
        "kept_trade_share": _safe_ratio(len(selected), len(trades)),
        "kept_net_pnl_usdt": sum(trade.pnl for trade in selected),
        "gross_profit_preservation": _safe_ratio(kept_profit, base_profit),
        "gross_loss_reduction": 1.0 - _safe_ratio(kept_loss, base_loss) if base_loss else 0.0,
        "kept_profit_factor": _safe_ratio(kept_profit, kept_loss),
        "kept_gross_profit_usdt": kept_profit,
        "kept_gross_loss_usdt": kept_loss,
    }


def _chronological_split(trades: list[Trade]) -> tuple[list[Trade], list[Trade]]:
    ordered = sorted(trades, key=lambda trade: trade.ts)
    split = max(1, min(len(ordered) - 1, len(ordered) // 2))
    return ordered[:split], ordered[split:]


def _single_numeric_repairs(trades: list[Trade]) -> list[dict[str, Any]]:
    if len(trades) < 12:
        return []
    train, test = _chronological_split(trades)
    feature_counts: Counter[str] = Counter()
    values_by_feature: defaultdict[str, list[float]] = defaultdict(list)
    for trade in train:
        for key, value in trade.numeric.items():
            feature_counts[key] += 1
            values_by_feature[key].append(value)

    candidates: list[dict[str, Any]] = []
    for feature, count in feature_counts.items():
        if count < max(8, int(0.75 * len(train))):
            continue
        values = values_by_feature[feature]
        if len(set(round(value, 12) for value in values)) < 4:
            continue
        thresholds = sorted({
            _quantile(values, q)
            for q in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
        })
        best: tuple[float, str, float, dict[str, Any]] | None = None
        for threshold in thresholds:
            for op in ("<=", ">"):
                keep = [
                    (trade.numeric.get(feature, math.nan) <= threshold)
                    if op == "<=" else
                    (trade.numeric.get(feature, math.nan) > threshold)
                    for trade in train
                ]
                stats = _evaluate_keep(train, keep)
                if stats["kept_trade_share"] < 0.35:
                    continue
                # Value gross-profit preservation more than raw in-sample PnL.
                utility = (
                    2.0 * stats["gross_profit_preservation"]
                    + 1.5 * stats["gross_loss_reduction"]
                    + 0.5 * stats["kept_trade_share"]
                    + 0.25 * min(stats["kept_profit_factor"], 5.0)
                )
                if best is None or utility > best[0]:
                    best = (utility, op, threshold, stats)
        if best is None:
            continue
        utility, op, threshold, train_stats = best
        test_keep = [
            (trade.numeric.get(feature, math.nan) <= threshold)
            if op == "<=" else
            (trade.numeric.get(feature, math.nan) > threshold)
            for trade in test
        ]
        test_stats = _evaluate_keep(test, test_keep)
        full_keep = [
            (trade.numeric.get(feature, math.nan) <= threshold)
            if op == "<=" else
            (trade.numeric.get(feature, math.nan) > threshold)
            for trade in trades
        ]
        full_stats = _evaluate_keep(trades, full_keep)
        # A repair is interesting only if the second half independently preserves
        # most of the winning engine while reducing losses.
        validated = (
            test_stats["kept_trades"] >= 3
            and test_stats["gross_profit_preservation"] >= 0.60
            and test_stats["gross_loss_reduction"] >= 0.15
            and full_stats["kept_trade_share"] >= 0.40
        )
        candidates.append({
            "feature": feature,
            "operator": op,
            "threshold": threshold,
            "train": train_stats,
            "test": test_stats,
            "full": full_stats,
            "validated_on_second_half": validated,
            "train_utility": utility,
        })
    candidates.sort(
        key=lambda row: (
            not row["validated_on_second_half"],
            -row["test"]["gross_loss_reduction"],
            -row["test"]["gross_profit_preservation"],
            -row["full"]["kept_net_pnl_usdt"],
        )
    )
    return candidates[:12]


def _categorical_repairs(trades: list[Trade]) -> list[dict[str, Any]]:
    if len(trades) < 8:
        return []
    keys = sorted({key for trade in trades for key in trade.categorical})
    candidates: list[dict[str, Any]] = []
    for key in keys:
        values = sorted({trade.categorical.get(key, "MISSING") for trade in trades})
        if len(values) <= 1 or len(values) > 40:
            continue
        for value in values:
            keep = [trade.categorical.get(key, "MISSING") != value for trade in trades]
            stats = _evaluate_keep(trades, keep)
            if stats["kept_trade_share"] < 0.35:
                continue
            # Removing a category is useful when it cuts materially more loss
            # than profit. This is diagnosis, not automatic permission to filter.
            differential = stats["gross_loss_reduction"] - (1.0 - stats["gross_profit_preservation"])
            if differential <= 0.05:
                continue
            candidates.append({
                "remove_feature": key,
                "remove_value": value,
                "loss_minus_profit_reduction": differential,
                **stats,
            })
    candidates.sort(
        key=lambda row: (
            -row["loss_minus_profit_reduction"],
            -row["gross_loss_reduction"],
            -row["gross_profit_preservation"],
        )
    )
    return candidates[:12]


def _load_run(metrics_path: Path) -> dict[str, Any] | None:
    scenarios_path = metrics_path.with_name("closed_scenarios.json")
    if not scenarios_path.is_file():
        return None
    try:
        metrics = _load(metrics_path)
        raw_scenarios = _load(scenarios_path)
    except Exception as exc:  # noqa: BLE001 - audit must continue across old evidence
        return {
            "path": str(metrics_path.relative_to(ROOT)),
            "classification": "IMPLEMENTATION_OR_EVIDENCE_PARSE_BLOCKED",
            "error": repr(exc),
        }
    if not isinstance(raw_scenarios, list):
        return None

    valid: list[Trade] = []
    invalid_count = 0
    zero_pnl_count = 0
    for item in raw_scenarios:
        if not isinstance(item, dict):
            continue
        if not _is_economically_valid(item):
            invalid_count += 1
            continue
        pnl = _scenario_pnl(item)
        if pnl == 0.0:
            zero_pnl_count += 1
        valid.append(Trade(
            pnl=pnl,
            ts=_int(item.get("episode_ts") or item.get("closed_ts_event")),
            numeric=_numeric_features(item),
            categorical=_categorical_features(item),
            source=str(metrics_path.relative_to(ROOT)),
        ))

    days = max(1, _int(metrics.get("calendar_days") or metrics.get("active_days") or 1))
    starting_nav = max(1.0, _number(metrics.get("starting_nav"), 100000.0))
    summary = _trade_summary(valid, days, starting_nav)
    capacity = summary["gross_profit_capacity_per_day_nav"]
    density = summary["trade_density_per_day"]
    loss_burden = summary["gross_loss_burden_per_day_nav"]
    # This is a prioritization score, not a pass/fail gate. It rewards a large
    # already-observed winning engine, sufficient opportunity density, and a
    # loss burden that could create large upside if it is separable.
    opportunity_score = (
        100.0 * capacity
        * math.sqrt(max(density, 0.0))
        * (1.0 + min(loss_burden / max(capacity, 1e-12), 5.0))
    )
    numeric_repairs = _single_numeric_repairs(valid)
    categorical_repairs = _categorical_repairs(valid)
    validated = [row for row in numeric_repairs if row["validated_on_second_half"]]
    best_repair = validated[0] if validated else (numeric_repairs[0] if numeric_repairs else None)

    return {
        "path": str(metrics_path.relative_to(ROOT)),
        "scenario_path": str(scenarios_path.relative_to(ROOT)),
        "candidate": metrics.get("candidate"),
        "evaluation_start": metrics.get("evaluation_start"),
        "evaluation_end": metrics.get("evaluation_end"),
        "calendar_days": days,
        "starting_nav": starting_nav,
        "implementation_invalid_trade_count": invalid_count,
        "zero_pnl_trade_count": zero_pnl_count,
        "reported_metrics": {
            key: metrics.get(key)
            for key in (
                "ending_nav", "total_return", "geometric_daily_growth", "max_drawdown",
                "trades", "wins", "losses", "win_rate", "profit_factor",
                "gross_profit", "gross_loss", "expectancy_usdt",
            )
        },
        "valid_trade_summary": summary,
        "gross_alpha_capacity_daily_pct": 100.0 * capacity,
        "gross_loss_burden_daily_pct": 100.0 * loss_burden,
        "observed_net_daily_pct": 100.0 * summary["net_pnl_per_day_nav"],
        "opportunity_score": opportunity_score,
        "numeric_repair_candidates": numeric_repairs,
        "categorical_loss_concentrations": categorical_repairs,
        "best_simple_repair": best_repair,
    }


def main() -> None:
    runs: list[dict[str, Any]] = []
    for metrics_path in sorted(EVIDENCE.rglob("metrics.json")):
        result = _load_run(metrics_path)
        if result is not None:
            runs.append(result)

    valid_runs = [row for row in runs if "valid_trade_summary" in row]
    ranked = sorted(
        valid_runs,
        key=lambda row: (
            -float(row.get("opportunity_score") or 0.0),
            -float(row.get("gross_alpha_capacity_daily_pct") or 0.0),
            -float((row.get("valid_trade_summary") or {}).get("trade_density_per_day") or 0.0),
        ),
    )

    # Aggregate by state across all economically valid historical trades. This
    # can expose a profitable sub-engine hidden inside a losing whole strategy.
    state_groups: defaultdict[str, list[Trade]] = defaultdict(list)
    for metrics_path in sorted(EVIDENCE.rglob("metrics.json")):
        scenarios_path = metrics_path.with_name("closed_scenarios.json")
        if not scenarios_path.is_file():
            continue
        try:
            scenarios = _load(scenarios_path)
        except Exception:
            continue
        if not isinstance(scenarios, list):
            continue
        for item in scenarios:
            if not isinstance(item, dict) or not _is_economically_valid(item):
                continue
            state = str(item.get("state") or "UNKNOWN")
            state_groups[state].append(Trade(
                pnl=_scenario_pnl(item),
                ts=_int(item.get("episode_ts") or item.get("closed_ts_event")),
                numeric=_numeric_features(item),
                categorical=_categorical_features(item),
                source=str(scenarios_path.relative_to(ROOT)),
            ))

    state_rows: list[dict[str, Any]] = []
    for state, trades in state_groups.items():
        if len(trades) < 5:
            continue
        summary = _trade_summary(trades, 1, 100000.0)
        state_rows.append({
            "state": state,
            "sources": len({trade.source for trade in trades}),
            "summary": summary,
            "numeric_repair_candidates": _single_numeric_repairs(trades),
            "categorical_loss_concentrations": _categorical_repairs(trades),
        })
    state_rows.sort(
        key=lambda row: (
            -float(row["summary"]["gross_profit_usdt"]),
            -int(row["summary"]["trades"]),
        )
    )

    result = {
        "purpose": (
            "Retrospective non-binary audit. Rank systems by observed gross winning engine, "
            "opportunity density, loss concentration, and out-of-sample-simple repairability. "
            "A positive or negative final PnL is not itself a research verdict."
        ),
        "implementation_rule": (
            "Trades explicitly marked invalid by actual-fill or economic-validity contracts are excluded "
            "from logic analysis and counted separately."
        ),
        "runs_scanned": len(runs),
        "economically_analyzable_runs": len(valid_runs),
        "ranked_runs": ranked,
        "state_level_mining": state_rows,
        "top_priority_paths": [row["path"] for row in ranked[:20]],
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Candidate 51 retrospective profit/loss mining",
        "",
        "This audit deliberately does **not** classify a strategy by final PnL alone. It ranks the already-observed winning engine, opportunity density, loss burden, and whether a simple pre-entry separation preserves winners while removing losses on the later half of the same run.",
        "",
        "Explicitly invalid actual-fill/economic trades are excluded from logic analysis.",
        "",
        "## Highest-priority historical runs",
        "",
        "| rank | evidence | trades/day | gross profit/day NAV | gross loss/day NAV | net/day NAV | PF | validated simple repair |",
        "|---:|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for index, row in enumerate(ranked[:30], start=1):
        summary = row["valid_trade_summary"]
        repairs = row.get("numeric_repair_candidates") or []
        validated_count = sum(bool(item.get("validated_on_second_half")) for item in repairs)
        lines.append(
            f"| {index} | `{row['path']}` | {summary['trade_density_per_day']:.3f} | "
            f"{row['gross_alpha_capacity_daily_pct']:.3f}% | {row['gross_loss_burden_daily_pct']:.3f}% | "
            f"{row['observed_net_daily_pct']:.3f}% | {summary['profit_factor']:.3f} | "
            f"{'yes' if validated_count else 'no'} |"
        )
    lines.extend(["", "## Highest-gross-profit states", "", "| state | trades | gross profit | gross loss | PF | sources |", "|---|---:|---:|---:|---:|---:|"])
    for row in state_rows[:30]:
        summary = row["summary"]
        lines.append(
            f"| `{row['state']}` | {summary['trades']} | {summary['gross_profit_usdt']:.2f} | "
            f"{summary['gross_loss_usdt']:.2f} | {summary['profit_factor']:.3f} | {row['sources']} |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n")
    print(json.dumps({
        "runs_scanned": len(runs),
        "analyzable": len(valid_runs),
        "top_paths": [row["path"] for row in ranked[:10]],
    }, indent=2))


if __name__ == "__main__":
    main()
