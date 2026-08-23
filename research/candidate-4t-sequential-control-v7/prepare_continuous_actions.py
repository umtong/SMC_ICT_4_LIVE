#!/usr/bin/env python3
"""Combine computational harvest shards into one chronological account stream.

Sharding is only a data-extraction optimization. Each shard includes warmup and a
post-boundary label tail. This script keeps actions whose order time belongs to the
shard's declared evaluation interval, deduplicates stable action IDs and assigns one
period name so the router cannot reset the account between shards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

REQUIRED = {"action_id", "state_id", "episode_id", "order_time_ns"}


def to_ns(value: str) -> int:
    return int(pd.Timestamp(value, tz="UTC").value)


def load_manifest(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("manifest must be a list")
    return data


def find_action_tables(root: Path) -> list[Path]:
    output: list[Path] = []
    for path in sorted(root.rglob("*.csv")):
        try:
            columns = set(pd.read_csv(path, nrows=1).columns)
        except Exception:
            continue
        if REQUIRED.issubset(columns):
            output.append(path)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    pieces: list[pd.DataFrame] = []
    source_hashes: dict[str, str] = {}
    shard_stats: list[dict[str, Any]] = []
    for shard in manifest:
        name = str(shard["name"])
        shard_root = args.root / name
        if not shard_root.exists():
            # actions/download-artifact commonly adds an artifact-name directory.
            matches = [path for path in args.root.rglob("*") if path.is_dir() and name in path.name]
            if not matches:
                raise FileNotFoundError(f"missing shard {name} below {args.root}")
            shard_root = sorted(matches)[0]
        start_ns = to_ns(str(shard["evaluation_start"]))
        end_ns = to_ns(str(shard["evaluation_end"]))
        tables = find_action_tables(shard_root)
        if not tables:
            raise FileNotFoundError(f"no immutable action table in {shard_root}")
        shard_rows = 0
        for table in tables:
            frame = pd.read_csv(table, low_memory=False)
            order_time = pd.to_numeric(frame.order_time_ns, errors="coerce")
            frame = frame[(order_time >= start_ns) & (order_time < end_ns)].copy()
            if frame.empty:
                continue
            frame["period"] = args.period
            frame["evaluation_shard"] = name
            frame["evaluation_shard_start_ns"] = start_ns
            frame["evaluation_shard_end_ns"] = end_ns
            pieces.append(frame)
            shard_rows += len(frame)
            source_hashes[str(table.relative_to(args.root))] = hashlib.sha256(table.read_bytes()).hexdigest()
        shard_stats.append({
            "name": name,
            "evaluation_start": shard["evaluation_start"],
            "evaluation_end": shard["evaluation_end"],
            "action_rows": int(shard_rows),
        })
    if not pieces:
        raise RuntimeError("no actions survived the declared continuous evaluation intervals")
    combined = pd.concat(pieces, ignore_index=True, sort=False)
    combined["order_time_ns"] = pd.to_numeric(combined.order_time_ns, errors="coerce")
    combined = combined.sort_values(["order_time_ns", "action_id"])
    before = len(combined)
    combined = combined.drop_duplicates(["action_id"], keep="first")
    # Stable state/action IDs should deduplicate overlap. A state may legitimately own
    # several entry actions, so state_id is not a uniqueness key.
    combined = combined.reset_index(drop=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.output, index=False)
    record = {
        "period": args.period,
        "rows_before_action_deduplication": int(before),
        "rows_after_action_deduplication": int(len(combined)),
        "duplicate_actions_removed": int(before - len(combined)),
        "first_order_time_ns": int(combined.order_time_ns.min()),
        "last_order_time_ns": int(combined.order_time_ns.max()),
        "shards": shard_stats,
        "source_sha256": source_hashes,
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
