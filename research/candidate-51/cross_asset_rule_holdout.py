#!/usr/bin/env python3
"""Evaluate predeclared cross-market failed-auction rules on untouched cases.

Rules are categorical market-state sequences derived before this holdout:

1. SYSTEMIC_FULL_FLIP: all three peers participate in the attack minute and all
   three peers participate in the strictly later opposite initiative.
2. SUSTAINED_SYSTEMIC_FULL_FLIP: the same sequence, with the target instrument's
   later initiative surviving the complete three-bar observation window.
3. SUSTAINED_CROSS_MARKET_STATE_FLIP: a non-systemic attack followed by peer
   majority participation in a complete three-bar opposite initiative.

No numeric PnL threshold is searched and no rule is changed from holdout output.
This is a component-state diagnostic, not a new account backtest.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
CANDIDATE05 = HERE.parent / "candidate-05"
for path in (CANDIDATE05, HERE):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from timestamp_contract import install as install_timestamp_contract  # noqa: E402

install_timestamp_contract()

from cross_asset_failure_diagnostic import _load_bars, enrich_case  # noqa: E402


def _sustained(case: dict[str, Any]) -> bool:
    value = case.get("initiative_observations")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and int(number) == 3


def systemic_full_flip(case: dict[str, Any]) -> bool:
    return bool(case.get("attack_peer_unanimous_event")) and bool(
        case.get("later_initiative_peer_unanimous"),
    )


def sustained_systemic_full_flip(case: dict[str, Any]) -> bool:
    return _sustained(case) and systemic_full_flip(case)


def sustained_cross_market_state_flip(case: dict[str, Any]) -> bool:
    return _sustained(case) and bool(case.get("cross_market_state_flip"))


def _summary(
    cases: list[dict[str, Any]],
    name: str,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    selected = [case for case in cases if predicate(case)]
    wins = sum(bool(case["win"]) for case in selected)
    return {
        "rule": name,
        "trades": len(selected),
        "wins": wins,
        "losses": len(selected) - wins,
        "win_rate": wins / len(selected) if selected else 0.0,
        "component_pnl_usdt": sum(
            float(case["realized_pnl_usdt"]) for case in selected
        ),
        "scenario_ids": [str(case["scenario_id"]) for case in selected],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)

    source = json.loads(args.cases.read_text(encoding="utf-8"))
    raw_cases = list(source["cases"])
    bars, evidence = _load_bars(raw_cases, args.cache)
    enriched = [enrich_case(case, bars) for case in raw_cases]
    rejection = [case for case in enriched if case.get("branch") == "REJECTION"]

    rules = [
        _summary(rejection, "SYSTEMIC_FULL_FLIP", systemic_full_flip),
        _summary(
            rejection,
            "SUSTAINED_SYSTEMIC_FULL_FLIP",
            sustained_systemic_full_flip,
        ),
        _summary(
            rejection,
            "SUSTAINED_CROSS_MARKET_STATE_FLIP",
            sustained_cross_market_state_flip,
        ),
    ]
    result = {
        "schema": "candidate35-cross-asset-rule-holdout-v1",
        "claim_scope": (
            "UNTOUCHED_HISTORICAL_COMPONENT_DIAGNOSTIC_"
            "NO_NEW_ACCOUNT_PNL_OR_NAV_CLAIM"
        ),
        "cases": len(enriched),
        "rejection_cases": len(rejection),
        "source_labels": sorted({str(case["source_label"]) for case in enriched}),
        "predeclared_rules": rules,
        "cases_enriched": enriched,
        "source_evidence": evidence,
    }
    (args.output / "cross_asset_rule_holdout.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "cases_enriched" and key != "source_evidence"},
            indent=2,
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
