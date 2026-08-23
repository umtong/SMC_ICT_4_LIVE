#!/usr/bin/env python3
"""Candidate 4t v4: v3 policy with an exact causal feature audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import candidate_4t_policy_v3 as core


OUTCOME_COLUMNS = {
    "fill_state", "outcome", "filled", "resolved", "win", "net_r",
    "mfe_r", "mae_r", "holding_minutes", "entry_wait_minutes",
    "terminal_minutes_label", "resolution_time_ns", "fill_time_ns",
    "terminal_ns", "order_terminal_time_ns",
}
OUTCOME_PREFIXES = ("label_", "future_", "actual_", "diagnostic_", "realized_")


def exact_feature_contract(names: list[str], mode: str) -> None:
    violations: list[str] = []
    for name in names:
        base_name = name.split("=", 1)[0].lower()
        if base_name in OUTCOME_COLUMNS or base_name.startswith(OUTCOME_PREFIXES):
            violations.append(name)
    if violations:
        raise AssertionError(f"{mode} future/outcome leakage: {violations[:12]}")
    if mode == "ownership":
        geometry: list[str] = []
        for name in names:
            base_name = name.split("=", 1)[0].lower()
            if (
                base_name in core.base.ACTION_GEOMETRY
                or base_name.endswith("_rr")
                or any(token in base_name for token in (
                    "entry", "stop", "target", "route", "risk", "geometry"
                ))
            ):
                geometry.append(name)
        if geometry:
            raise AssertionError(f"ownership geometry leakage: {geometry[:12]}")


# All v3 calls resolve this global at runtime, so the hardened contract covers every model.
core._assert_feature_contract = exact_feature_contract


def run(development_root: Path, fresh_root: Path | None, output: Path) -> dict[str, Any]:
    result = core.run(development_root, fresh_root, output)
    result["policy"] = "CANDIDATE_4T_V4_CAUSAL_STATE_OWNERSHIP_GLOBAL_COMMITMENT"
    result["causal_feature_contract"] = (
        "exact outcome/label exclusion; no action geometry in ownership"
    )
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output / "RESULT.md").write_text(
        "# Candidate 4t v4 causal diagnostic result\n\n"
        "The state ownership and continuation models use the hardened exact-column "
        "causal feature contract. Development periods are leave-one-period-out; fresh "
        "data is not used for fitting or architecture selection.\n\n```json\n"
        + json.dumps(result, ensure_ascii=False, indent=2, default=str)
        + "\n```\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.development_root, args.fresh_root, args.output)


if __name__ == "__main__":
    main()
