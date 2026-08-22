#!/usr/bin/env python3
"""Copy compact raw backtest evidence from matrix artifacts into the branch.

This is deliberately not a scoring or promotion framework. It preserves the
actual summaries, completed trades and representative no-trade traces so the
policy can be revised from concrete market decisions.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path
import shutil
from typing import Any


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _open_csv(path: Path):  # type: ignore[no-untyped-def]
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def _sample_csv(source: Path, destination: Path, limit: int = 500) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    try:
        with _open_csv(source) as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or ())
            for row in reader:
                rows.append(dict(row))
                if len(rows) >= limit:
                    break
    except Exception:
        return 0
    if not rows or not fieldnames:
        return 0
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.output.exists():
        shutil.rmtree(args.output)
    args.output.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"summaries": [], "csv_samples": [], "json_samples": []}
    for path in sorted(args.input.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(args.input)
        lowered = path.name.lower()
        if lowered == "summary.json" or lowered.endswith("summary.json"):
            destination = args.output / "summaries" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            manifest["summaries"].append(
                {"source": str(relative), "destination": str(destination.relative_to(args.output)), "data": _read_json(path)}
            )
            continue
        if path.suffix.lower() in {".json", ".jsonl"} and any(
            token in lowered for token in ("diagnostic", "trace", "rejection", "abstention", "audit")
        ):
            destination = args.output / "json_samples" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                if path.suffix.lower() == ".jsonl":
                    lines = path.read_text(encoding="utf-8").splitlines()[:300]
                    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
                else:
                    data = _read_json(path)
                    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2)[:500_000], encoding="utf-8")
                manifest["json_samples"].append(str(destination.relative_to(args.output)))
            except Exception:
                pass
            continue
        is_csv = path.suffix.lower() == ".csv" or lowered.endswith(".csv.gz")
        if is_csv and any(
            token in lowered for token in ("trade", "action", "order", "episode", "plan", "miss", "abstain", "trace")
        ):
            clean_name = path.name[:-3] if path.name.endswith(".gz") else path.name
            destination = args.output / "csv_samples" / relative.parent / clean_name
            count = _sample_csv(path, destination)
            if count:
                manifest["csv_samples"].append(
                    {"source": str(relative), "destination": str(destination.relative_to(args.output)), "rows": count}
                )

    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
