"""Path and concentration diagnostics for continuous single-slot accounts.

These metrics do not change a trade decision.  They answer the user's central
robustness question: does the account remain profitable without its luckiest
trade or luckiest close-day, and how broadly is gross profit distributed?
"""
from __future__ import annotations

from collections.abc import Iterable
import math
from typing import Any

import numpy as np
import pandas as pd


def _compound_return(factors: Iterable[float]) -> float:
    value = 1.0
    for factor in factors:
        value *= float(factor)
    return value - 1.0


def _max_drawdown(factors: Iterable[float]) -> float:
    nav = 1.0
    peak = 1.0
    worst = 0.0
    for factor in factors:
        nav *= float(factor)
        peak = max(peak, nav)
        if peak > 0.0:
            worst = min(worst, nav / peak - 1.0)
    return worst


def _maximum_loss_streak(values: Iterable[float]) -> int:
    current = 0
    maximum = 0
    for value in values:
        if value < 0.0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def trade_robustness_metrics(
    trade_audit: pd.DataFrame,
    *,
    reported_total_return: float | None = None,
) -> dict[str, Any]:
    """Measure outlier dependence using the actual sequential account trades.

    The project enforces one pending order or position globally, so closed trades
    are sequential rather than overlapping.  ``realized_pnl / nav_at_submission``
    therefore supplies a directly interpretable multiplicative account factor.
    The counterfactuals set the best trade or best close-day factor to ``1.0``;
    they do not reorder trades or refit the strategy.
    """
    required = {
        "position_id",
        "realized_pnl",
        "nav_at_submission",
        "actual_net_r",
        "ts_closed",
    }
    missing = sorted(required - set(trade_audit.columns))
    if missing:
        raise ValueError(f"trade audit missing robustness columns: {missing}")
    if trade_audit.empty:
        return {
            "robustness_trade_count": 0,
            "robustness_status": "NO_TRADES",
        }

    frame = trade_audit.copy()
    frame["ts_closed_parsed"] = pd.to_datetime(frame["ts_closed"], utc=True, errors="raise")
    frame = frame.sort_values(["ts_closed_parsed", "position_id"], kind="stable").reset_index(drop=True)
    pnl = pd.to_numeric(frame["realized_pnl"], errors="raise").astype(float)
    nav = pd.to_numeric(frame["nav_at_submission"], errors="raise").astype(float)
    net_r = pd.to_numeric(frame["actual_net_r"], errors="raise").astype(float)
    if (nav <= 0.0).any():
        raise ValueError("non-positive NAV at submission in robustness audit")
    trade_returns = pnl / nav
    factors = 1.0 + trade_returns
    if (factors <= 0.0).any():
        raise ValueError("a trade factor is non-positive; account path is unrecoverable")

    compounded = _compound_return(factors)
    positive_pnl = pnl[pnl > 0.0].sort_values(ascending=False)
    gross_profit = float(positive_pnl.sum())
    gross_loss = float(-pnl[pnl < 0.0].sum())
    shares = positive_pnl / gross_profit if gross_profit > 0.0 else pd.Series(dtype=float)
    squared_share_sum = float(np.square(shares.to_numpy()).sum()) if len(shares) else 0.0
    effective_winners = 1.0 / squared_share_sum if squared_share_sum > 0.0 else 0.0

    ordered_best = trade_returns.sort_values(ascending=False).index.tolist()
    best_index = ordered_best[0]
    top_three = set(ordered_best[:3])
    best_removed = _compound_return(
        1.0 if index == best_index else factor
        for index, factor in enumerate(factors)
    )
    best_three_removed = _compound_return(
        1.0 if index in top_three else factor
        for index, factor in enumerate(factors)
    )

    close_days = frame["ts_closed_parsed"].dt.floor("D")
    daily_factors = (
        pd.DataFrame({"day": close_days, "factor": factors})
        .groupby("day", sort=True)["factor"]
        .prod()
    )
    best_day = daily_factors.idxmax()
    best_day_removed = _compound_return(
        1.0 if day == best_day else factor
        for day, factor in daily_factors.items()
    )

    result: dict[str, Any] = {
        "robustness_trade_count": int(len(frame)),
        "win_rate": float((pnl > 0.0).mean()),
        "profit_factor": None if gross_loss <= 0.0 else gross_profit / gross_loss,
        "mean_net_r": float(net_r.mean()),
        "median_net_r": float(net_r.median()),
        "sum_net_r": float(net_r.sum()),
        "maximum_net_r": float(net_r.max()),
        "minimum_net_r": float(net_r.min()),
        "maximum_consecutive_losses": _maximum_loss_streak(trade_returns),
        "trade_compounded_return": compounded,
        "trade_path_max_drawdown": _max_drawdown(factors),
        "gross_profit_top1_share": None if shares.empty else float(shares.iloc[0]),
        "gross_profit_top3_share": None if shares.empty else float(shares.iloc[:3].sum()),
        "effective_positive_trade_count": effective_winners,
        "best_trade_removed_compound_return": best_removed,
        "best_three_trades_removed_compound_return": best_three_removed,
        "best_trade_removed_still_profitable": bool(best_removed > 0.0),
        "positive_close_days": int((daily_factors > 1.0).sum()),
        "negative_close_days": int((daily_factors < 1.0).sum()),
        "flat_close_days": int((daily_factors == 1.0).sum()),
        "positive_close_day_fraction": float((daily_factors > 1.0).mean()),
        "best_close_day_return": float(daily_factors.max() - 1.0),
        "worst_close_day_return": float(daily_factors.min() - 1.0),
        "best_close_day_removed_compound_return": best_day_removed,
        "best_close_day_removed_still_profitable": bool(best_day_removed > 0.0),
    }
    if reported_total_return is not None and math.isfinite(float(reported_total_return)):
        result["trade_compound_reproduction_error"] = compounded - float(reported_total_return)
    result["robustness_status"] = (
        "NON_SINGLE_OUTLIER_PROFIT"
        if result["best_trade_removed_still_profitable"]
        and result["best_close_day_removed_still_profitable"]
        else "OUTLIER_DEPENDENT"
    )
    return result
