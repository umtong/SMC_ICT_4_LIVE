"""Exact audit-role reconciliation for native opposite-context exits.

NautilusTrader's ``close_all_positions`` produces an exchange-native market
closing order without the bracket's user ``ROLE:*`` tag.  The strategy records
the exact client order ID only when that market fill was caused by a live
confirmed-opposite 1h context request.  This adapter joins that ID to the
position's native closing order; it never infers a role from profit, timestamp
proximity, price or outcome.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

import mtf_backtest_support as _base


_ORIGINAL_BUILD_AUDIT = _base._build_mtf_trade_audit


def _build_context_exit_trade_audit(
    strategy: Any,
    orders_export: pd.DataFrame,
    positions_export: pd.DataFrame,
    evaluation_end: date,
) -> pd.DataFrame:
    audit = _ORIGINAL_BUILD_AUDIT(
        strategy,
        orders_export,
        positions_export,
        evaluation_end,
    )
    if audit.empty:
        return audit
    exact_client_ids = {
        str(event["client_order_id"])
        for event in strategy.event_log
        if event.get("kind") == "context_exit_order_filled"
        and event.get("client_order_id")
    }
    if not exact_client_ids:
        return audit
    mask = (
        audit["exit_role"].isna()
        & audit["closing_order_id"].astype(str).isin(exact_client_ids)
    )
    audit.loc[mask, "exit_role"] = "CONTEXT_EXIT"
    return audit


def preserve_mtf_results(
    engine,
    strategy,
    output: Path,
    *,
    symbols: tuple[str, ...],
    start: date,
    end: date,
):
    """Run the standard evidence writer with one exact additional exit role."""
    previous = _base._build_mtf_trade_audit
    _base._build_mtf_trade_audit = _build_context_exit_trade_audit
    try:
        return _base.preserve_mtf_results(
            engine,
            strategy,
            output,
            symbols=symbols,
            start=start,
            end=end,
        )
    finally:
        _base._build_mtf_trade_audit = previous


__all__ = [
    "_build_context_exit_trade_audit",
    "preserve_mtf_results",
]
