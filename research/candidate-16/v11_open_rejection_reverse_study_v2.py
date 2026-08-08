#!/usr/bin/env python3
"""Slots-dataclass serialization repair for the frozen v11 study.

``RejectionCandidate`` is intentionally a frozen slots dataclass and therefore
has no ``__dict__``.  This launcher changes only evidence serialization to
``dataclasses.asdict``.  Profile, state, transition, entry, stop, target, costs,
outcome and promotion rules remain unchanged.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

import pandas as pd

import v11_open_rejection_reverse_study as base


def records(scored: list[base.ScoredRejection]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in scored:
        rows.append(
            {
                **asdict(item.candidate),
                "exit_ts": item.exit_ts,
                "exit_reason": item.exit_reason,
                "exit_price": item.exit_price,
                "net_return": item.net_return,
                "net_r": item.net_r,
                "mfe": item.mfe,
                "mae": item.mae,
            },
        )
    return pd.DataFrame(rows)


base.records = records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = base.run(args.cache.resolve(), args.output.resolve())
    result["evidence_serialization"] = {
        "candidate_dataclass": "frozen slots",
        "method": "dataclasses.asdict",
        "economic_logic_changed": False,
    }
    base.write_json(args.output / "study.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
