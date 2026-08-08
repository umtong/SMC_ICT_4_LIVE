"""Narrow runtime compatibility patch for the pinned Candidate 16 v9 study.

Only active when that module is present on PYTHONPATH.  The upstream logic used
Timestamp.min as a sentinel; subtracting it from modern data now overflows
pandas Timedelta.  None preserves the exact first-event semantics.
"""
from __future__ import annotations

try:
    import pandas as pd
    import v9_tardis_liquidation_study as upstream
except ImportError:
    upstream = None


if upstream is not None:
    original = upstream._within_symbol_decluster
    if not getattr(original, "_candidate18_safe_first_event", False):
        def within_symbol_decluster_safe(panel: pd.DataFrame) -> pd.DataFrame:
            kept: list[int] = []
            horizon = pd.Timedelta(minutes=upstream.WITHIN_SYMBOL_DECLUSTER_MINUTES)
            for _symbol, group in panel.groupby("symbol", sort=False):
                positions = group.index[group["event_candidate"]].tolist()
                last: dict[int, pd.Timestamp | None] = {-1: None, 1: None}
                for position in positions:
                    row = panel.loc[position]
                    direction = int(row["event_direction"])
                    moment = pd.Timestamp(row["minute"])
                    previous = last[direction]
                    if previous is not None and moment - previous < horizon:
                        continue
                    kept.append(position)
                    last[direction] = moment
            if not kept:
                return pd.DataFrame()
            return panel.loc[sorted(kept)].copy()

        within_symbol_decluster_safe._candidate18_safe_first_event = True  # type: ignore[attr-defined]
        upstream._within_symbol_decluster = within_symbol_decluster_safe
