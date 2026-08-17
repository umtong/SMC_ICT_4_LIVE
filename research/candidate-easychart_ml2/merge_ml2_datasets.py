#!/usr/bin/env python3
"""Merge non-overlapping ML2 shadow datasets into one chronological table.

Plan counters restart in each independent Nautilus research process.  This tool
therefore namespaces plan and causal-event identities by the source dataset
checksum before concatenation.  It rejects overlapping decision-time ranges so
the same market episode cannot silently appear twice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from ml2_features import FEATURE_NAMES


MERGE_POLICY = (
    "NON_OVERLAPPING_CHRONOLOGICAL_CHUNKS; SOURCE_CHECKSUM_NAMESPACES_PROCESS_LOCAL_"
    "PLAN_AND_CAUSAL_EVENT_IDENTITIES; EXACT_FEATURE_SCHEMA; NO_SILENT_DEDUPLICATION"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    return parser.parse_args()


def merge_datasets(
    *,
    inputs: list[Path],
    output: Path,
    summary: Path,
) -> dict[str, Any]:
    if not inputs:
        raise ValueError("at least one input dataset is required")
    required = {
        "plan_id",
        "event_group_id",
        "decision_bucket_id",
        "event_time_ns",
        "label_end_ns",
        "symbol",
        "family",
        "label",
        *(f"ml2f_{name}" for name in FEATURE_NAMES),
    }

    records: list[tuple[int, int, Path, str, pd.DataFrame]] = []
    canonical_columns: list[str] | None = None
    for path in inputs:
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            raise RuntimeError(f"input dataset is empty: {path}")
        missing = sorted(required - set(frame.columns))
        if missing:
            raise RuntimeError(f"{path} missing required columns: {missing[:30]}")
        if canonical_columns is None:
            canonical_columns = list(frame.columns)
        elif set(frame.columns) != set(canonical_columns):
            missing_here = sorted(set(canonical_columns) - set(frame.columns))
            extra_here = sorted(set(frame.columns) - set(canonical_columns))
            raise RuntimeError(
                f"dataset schema differs for {path}; missing={missing_here[:20]} "
                f"extra={extra_here[:20]}",
            )
        frame = frame[canonical_columns].copy()
        frame["event_time_ns"] = pd.to_numeric(frame["event_time_ns"], errors="raise").astype("int64")
        source_sha = sha256_file(path)
        source_id = source_sha[:16]
        records.append(
            (
                int(frame["event_time_ns"].min()),
                int(frame["event_time_ns"].max()),
                path,
                source_id,
                frame,
            ),
        )

    records.sort(key=lambda item: (item[0], item[1], str(item[2])))
    prior_end: int | None = None
    for start, end, path, _, _ in records:
        if prior_end is not None and start <= prior_end:
            raise RuntimeError(
                "input dataset decision-time ranges overlap; rerun with disjoint trading chunks: "
                f"{path} starts at {start} while prior chunk ends at {prior_end}",
            )
        prior_end = end

    merged_frames: list[pd.DataFrame] = []
    source_summaries: list[dict[str, Any]] = []
    for start, end, path, source_id, frame in records:
        local = frame.copy()
        local.insert(0, "dataset_source_id", source_id)
        local.insert(1, "source_plan_id", local["plan_id"].astype(str))
        local["plan_id"] = source_id + "|" + local["plan_id"].astype(str)
        local["event_group_id"] = source_id + "|" + local["event_group_id"].astype(str)
        merged_frames.append(local)
        source_summaries.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "source_id": source_id,
                "rows": int(len(local)),
                "start_ns": start,
                "end_ns": end,
                "start": pd.Timestamp(start, unit="ns", tz="UTC").isoformat(),
                "end": pd.Timestamp(end, unit="ns", tz="UTC").isoformat(),
            },
        )

    merged = pd.concat(merged_frames, ignore_index=True, sort=False)
    merged = merged.sort_values(
        ["event_time_ns", "symbol", "plan_id"],
        kind="mergesort",
    ).reset_index(drop=True)
    if merged["plan_id"].duplicated().any():
        raise RuntimeError("namespaced plan_id collision after merge")
    if not merged["event_time_ns"].is_monotonic_increasing:
        raise RuntimeError("merged event time is not chronological")

    output.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output, index=False)
    resolved = merged[merged["label"].notna()]
    result: dict[str, Any] = {
        "policy": MERGE_POLICY,
        "inputs": source_summaries,
        "rows": int(len(merged)),
        "resolved_rows": int(len(resolved)),
        "causal_events": int(merged["event_group_id"].nunique()),
        "decision_buckets": int(merged["decision_bucket_id"].nunique()),
        "start": pd.Timestamp(int(merged["event_time_ns"].min()), unit="ns", tz="UTC").isoformat(),
        "end": pd.Timestamp(int(merged["event_time_ns"].max()), unit="ns", tz="UTC").isoformat(),
        "feature_count": len(FEATURE_NAMES),
        "output": str(output),
        "output_sha256": sha256_file(output),
        "by_symbol": {
            str(key): int(len(group))
            for key, group in merged.groupby("symbol", sort=True)
        },
        "by_causal_family": {
            str(key): int(len(group))
            for key, group in merged.groupby("ml2_causal_family", sort=True)
        },
    }
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    args = parse_args()
    result = merge_datasets(inputs=args.inputs, output=args.output, summary=args.summary)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
