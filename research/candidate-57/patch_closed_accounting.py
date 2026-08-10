#!/usr/bin/env python3
"""Patch Candidate 51's reused runner to count completed trades only.

The original generic metrics path treated an open position snapshot as a
completed loss and allowed a non-flat replay to satisfy its position-count
check.  Candidate 57 uses this one-off migration because many existing
strategies reuse the same Nautilus runner.
"""
from __future__ import annotations

from pathlib import Path


PATH = Path("research/candidate-51/run.py")


def patch() -> None:
    text = PATH.read_text(encoding="utf-8")
    if "def _closed_position_rows(" in text:
        print("closed-accounting patch already present")
        compile(text, str(PATH), "exec")
        return

    helper = '''

def _normalized_column(frame: pd.DataFrame, needle: str) -> Any | None:
    normalized = {
        str(column).lower().replace(" ", "_"): column
        for column in frame.columns
    }
    return next(
        (original for name, original in normalized.items() if needle in name),
        None,
    )


def _closed_position_rows(positions: pd.DataFrame) -> pd.DataFrame:
    """Return completed position rows only; open snapshots are not trades."""
    if positions.empty:
        return positions.copy()
    side_column = _normalized_column(positions, "side")
    closed_column = _normalized_column(positions, "ts_closed")
    if side_column is None or closed_column is None:
        raise Candidate35RunError(
            f"positions report lacks side/ts_closed columns: {list(positions.columns)}"
        )
    side = positions[side_column].astype(str).str.strip().str.upper()
    closed_text = positions[closed_column].astype(str).str.strip().str.lower()
    closed = (
        side.eq("FLAT")
        & positions[closed_column].notna()
        & ~closed_text.isin({"", "nan", "none", "nat"})
    )
    return positions.loc[closed].copy()


def _open_position_rows(positions: pd.DataFrame) -> pd.DataFrame:
    """Return unresolved position rows at the end of the replay."""
    if positions.empty:
        return positions.copy()
    side_column = _normalized_column(positions, "side")
    closed_column = _normalized_column(positions, "ts_closed")
    if side_column is None or closed_column is None:
        raise Candidate35RunError(
            f"positions report lacks side/ts_closed columns: {list(positions.columns)}"
        )
    side = positions[side_column].astype(str).str.strip().str.upper()
    closed_text = positions[closed_column].astype(str).str.strip().str.lower()
    unresolved = (
        ~side.eq("FLAT")
        | positions[closed_column].isna()
        | closed_text.isin({"", "nan", "none", "nat"})
    )
    return positions.loc[unresolved].copy()


def _active_order_rows(orders: pd.DataFrame) -> pd.DataFrame:
    """Return orders still live/inflight when the replay ends."""
    if orders.empty:
        return orders.copy()
    status_column = _normalized_column(orders, "status")
    if status_column is None:
        raise Candidate35RunError(
            f"orders report lacks status column: {list(orders.columns)}"
        )
    terminal = {
        "FILLED",
        "CANCELED",
        "CANCELLED",
        "REJECTED",
        "DENIED",
        "EXPIRED",
    }
    status = orders[status_column].astype(str).str.strip().str.upper()
    return orders.loc[~status.isin(terminal)].copy()
'''
    marker = "\ndef _rolling(daily: dict[str, float], starting_nav: float, window: int) -> dict[str, Any]:\n"
    if marker not in text:
        raise RuntimeError("rolling insertion marker not found")
    text = text.replace(marker, helper + marker, 1)

    replacements = [
        (
            '''def build_metrics(
    *, equity: pd.DataFrame, positions: pd.DataFrame, output: Path,
    start: date, end: date, config: dict[str, Any], result: Any,
    input_records: dict[str, Any],
) -> dict[str, Any]:''',
            '''def build_metrics(
    *, equity: pd.DataFrame, positions: pd.DataFrame, orders: pd.DataFrame, output: Path,
    start: date, end: date, config: dict[str, Any], result: Any,
    input_records: dict[str, Any],
) -> dict[str, Any]:''',
        ),
        (
            "    pnls = c05.extract_position_pnls(positions)\n",
            '''    closed_positions = _closed_position_rows(positions)
    open_positions = _open_position_rows(positions)
    active_orders = _active_order_rows(orders)
    pnls = c05.extract_position_pnls(closed_positions)
''',
        ),
        (
            '        "nautilus_positions_match": int(result.total_positions) == trades,\n',
            '''        "closed_position_rows_match_trade_count": len(closed_positions) == trades,
        "no_open_positions_at_end": open_positions.empty,
        "no_active_orders_at_end": active_orders.empty,
        "nautilus_position_report_rows_match": int(result.total_positions) == len(positions),
''',
        ),
        (
            '        "position_counts_by_symbol": _symbol_counts(positions),\n',
            '''        "position_counts_by_symbol": _symbol_counts(closed_positions),
        "position_report_rows": len(positions),
        "closed_position_rows": len(closed_positions),
        "open_position_rows_at_end": len(open_positions),
        "active_order_rows_at_end": len(active_orders),
        "active_order_status_counts": (
            active_orders[_normalized_column(active_orders, "status")]
            .astype(str)
            .str.strip()
            .str.upper()
            .value_counts()
            .to_dict()
            if not active_orders.empty
            else {}
        ),
''',
        ),
        (
            '''        metrics = build_metrics(
            equity=equity,
            positions=positions,
            output=output,''',
            '''        metrics = build_metrics(
            equity=equity,
            positions=positions,
            orders=orders,
            output=output,''',
        ),
    ]
    for old, new in replacements:
        if old not in text:
            raise RuntimeError(f"accounting patch marker not found: {old[:80]!r}")
        text = text.replace(old, new, 1)

    compile(text, str(PATH), "exec")
    PATH.write_text(text, encoding="utf-8")
    print("patched closed-only PnL accounting and mandatory end-flat checks")


if __name__ == "__main__":
    patch()
