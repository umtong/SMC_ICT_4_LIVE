#!/usr/bin/env python3
"""Canonicalize conflicting Binance metrics rows by archive ownership.

A daily archive can repeat the next UTC midnight metric which also belongs to
the next day's archive. When repeated values conflict, the archive whose date
matches the metric create_time owns the observation. Non-boundary duplicates
remain subject to the frozen conflict check.
"""
from pathlib import Path
import sys


path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
marker = "V41_CANONICAL_METRICS_ARCHIVE_OWNER"
if marker in text:
    raise SystemExit(0)
anchor = "\ndef load_range(\n"
helper = r'''

# V41_CANONICAL_METRICS_ARCHIVE_OWNER
def _canonicalize_archive_boundaries(metrics: pd.DataFrame) -> pd.DataFrame:
    required = {"metrics_observed_time", "_source_archive_day"}
    if not required.issubset(metrics.columns):
        raise RuntimeError("metrics archive ownership columns are missing")
    frame = metrics.sort_values(
        ["metrics_observed_time", "_source_archive_day"],
        kind="stable",
    ).copy()
    event_day = (
        frame["metrics_observed_time"] - pd.Timedelta(minutes=5)
    ).dt.strftime("%Y-%m-%d")
    frame["_canonical_archive_owner"] = (
        event_day == frame["_source_archive_day"].astype(str)
    )
    pieces: list[pd.DataFrame] = []
    for _, group in frame.groupby("metrics_observed_time", sort=False):
        if len(group) == 1:
            pieces.append(group)
            continue
        owners = group[group["_canonical_archive_owner"]]
        pieces.append(owners if not owners.empty else group)
    result = pd.concat(pieces, ignore_index=True)
    return result.drop(
        columns=["_source_archive_day", "_canonical_archive_owner"],
    )
'''
if anchor not in text:
    raise RuntimeError("positioning load_range anchor not found")
text = text.replace(anchor, helper + anchor, 1)
old_append = "        metric_frames.append(_read_metrics(archive))\n"
new_append = (
    "        metric_frame = _read_metrics(archive)\n"
    "        metric_frame[\"_source_archive_day\"] = day.isoformat()\n"
    "        metric_frames.append(metric_frame)\n"
)
if old_append not in text:
    raise RuntimeError("positioning metric append line not found")
text = text.replace(old_append, new_append, 1)
old_metrics = "    metrics = _positioning_features(pd.concat(metric_frames, ignore_index=True))\n"
new_metrics = (
    "    combined_metrics = _canonicalize_archive_boundaries(\n"
    "        pd.concat(metric_frames, ignore_index=True),\n"
    "    )\n"
    "    metrics = _positioning_features(combined_metrics)\n"
)
if old_metrics not in text:
    raise RuntimeError("positioning combined metrics line not found")
text = text.replace(old_metrics, new_metrics, 1)
path.write_text(text, encoding="utf-8")
