"""Candidate 13 v4 runner materialization.

The trading boundary remains the v3 materializer.  This layer fixes one old
Binance archive compatibility defect before any strategy code runs: historical
daily files can preserve a CSV header token beside integer timestamps, so
``open_time`` must be assigned back as strict int64 before sorting.  The patch
is exact-count and fail-closed.
"""
from __future__ import annotations

from runner_materializer import materialize_runner_source as materialize_v3


OLD_TIMESTAMP_BLOCK = '''        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()
        if len(frame.index) not in (1439, 1440, 1441):'''

NEW_TIMESTAMP_BLOCK = '''        numeric_open_time = pd.to_numeric(frame["open_time"], errors="coerce")
        valid_open_time = numeric_open_time.notna()
        frame = frame.loc[valid_open_time].copy()
        frame["open_time"] = numeric_open_time.loc[valid_open_time].astype("int64")
        # candidate-13-strict-open-time: mixed header/integer archives normalize before sort.
        if len(frame.index) not in (1439, 1440, 1441):'''


def materialize_runner_source(source: str) -> str:
    occurrences = source.count(OLD_TIMESTAMP_BLOCK)
    if occurrences != 1:
        raise RuntimeError(
            "Candidate 13 timestamp-normalization contract drifted: "
            f"expected one block, found {occurrences}",
        )
    normalized = source.replace(OLD_TIMESTAMP_BLOCK, NEW_TIMESTAMP_BLOCK, 1)
    materialized = materialize_v3(normalized)
    if materialized.count("candidate-13-strict-open-time") != 1:
        raise RuntimeError("strict open_time normalization was not materialized exactly once")
    return materialized
