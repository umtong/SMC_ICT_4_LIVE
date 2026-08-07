#!/usr/bin/env python3
"""Select one predeclared scenario family without touching execution results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROUTES: dict[str, dict[str, set[str] | None]] = {
    "v33": {
        "full": None,
    },
    "v34": {
        "full": None,
        "continuation": {"INFORMED_INVENTORY_PULLBACK_CONTINUATION"},
        "reversal": {
            "POST_ATTACK_LIQUIDATION_ABSORPTION_REVERSAL",
            "POST_ATTACK_TRAPPED_INVENTORY_REVERSAL",
        },
    },
    "v35": {
        "full": None,
        "continuation": {"EVENT_TIME_INFORMED_FLOW_PULLBACK_CONTINUATION"},
        "reversal": {"EVENT_TIME_EXTERNAL_LIQUIDITY_ABSORPTION_REVERSAL"},
    },
    "v36": {
        "full": None,
        "continuation": {
            "MICRO_BALANCE_NEW_INVENTORY_RETEST_CONTINUATION",
            "MICRO_BALANCE_LIQUIDATION_RETEST_CONTINUATION",
        },
        "reversal": {
            "MICRO_BALANCE_TRAPPED_BREAKOUT_REVERSAL",
            "MICRO_BALANCE_LIQUIDATION_EXHAUSTION_REVERSAL",
        },
    },
    "v37": {
        "full": None,
        "continuation": {
            "VOLUME_CLOCK_INFORMED_INVENTORY_PULLBACK_CONTINUATION",
        },
        "reversal": {
            "VOLUME_CLOCK_TRAPPED_INVENTORY_ABSORPTION_REVERSAL",
            "VOLUME_CLOCK_LIQUIDATION_ABSORPTION_REVERSAL",
        },
    },
    "v38": {
        "full": None,
        "continuation": {
            "VOLUME_CLOCK_INFORMED_GAP_RETEST_CONTINUATION",
        },
        "reversal": {
            "VOLUME_CLOCK_TRAPPED_INVENTORY_INVERSE_GAP_REVERSAL",
            "VOLUME_CLOCK_LIQUIDATION_INVERSE_GAP_REVERSAL",
        },
    },
    "v41": {
        "full": None,
        "continuation": {
            "DEPTH_NORMALIZED_POSITIVE_INNOVATION_PULLBACK_CONTINUATION",
        },
        "reversal": {
            "EXTERNAL_POOL_NEGATIVE_INNOVATION_TRAPPED_REVERSAL",
            "EXTERNAL_POOL_NEGATIVE_INNOVATION_LIQUIDATION_REVERSAL",
        },
    },
}


def filter_rows(
    rows: list[dict],
    family: str,
    route: str,
) -> list[dict]:
    try:
        scenarios = ROUTES[family][route]
    except KeyError as exc:
        raise ValueError(f"unsupported candidate route: {family}/{route}") from exc
    if scenarios is None:
        return list(rows)
    return [row for row in rows if str(row.get("scenario")) in scenarios]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--family", choices=sorted(ROUTES), required=True)
    parser.add_argument("--route", required=True)
    args = parser.parse_args()

    rows = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not all(
        isinstance(row, dict) for row in rows
    ):
        raise SystemExit("signals input must be a list of objects")
    selected = filter_rows(rows, args.family, args.route)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "signals.json").write_text(
        json.dumps(selected, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            {
                "written_signals": len(selected),
                "route_counts": {
                    "candidate_family": args.family,
                    "frozen_route": args.route,
                    "input_signals": len(rows),
                    "removed_signals": len(rows) - len(selected),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "family": args.family,
                "route": args.route,
                "input": len(rows),
                "written": len(selected),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
