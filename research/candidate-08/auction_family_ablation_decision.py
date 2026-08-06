"""Predeclared one-family ablation decision for candidate-08 auction-router failures.

The selector does not choose the better of many variants. It permits exactly one diagnostic
counterfactual only when the base evidence cleanly separates one independently positive family from
one independently negative family. If both families are negative, both are positive, either is
untraded, attribution is incomplete, or the base run is otherwise invalid, there is no family-level
ablation path.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping


INITIATIVE_FAMILY = "INITIATIVE_ACCEPTANCE_CONTINUATION"
FAILED_AUCTION_FAMILY = "FAILED_AUCTION_REVERSAL"
IMPLEMENTATION_REVISION = "FAILED_AUCTION_FULL_OBSERVED_SWEEP_THROUGH_CONFIRMATION_V2"
FAMILY_TO_MODE = {
    INITIATIVE_FAMILY: "initiative_only",
    FAILED_AUCTION_FAMILY: "failed_auction_only",
}


@dataclass(frozen=True, slots=True)
class FamilyContribution:
    family: str
    signals: int
    closed_trades: int
    wins: int
    realized_pnl_usdt: float


@dataclass(frozen=True, slots=True)
class AblationDecision:
    selected: bool
    reason: str
    suite: str
    family_mode: str | None
    retained_family: str | None
    removed_family: str | None
    contributions: tuple[FamilyContribution, ...]


def _contribution(
    family: str,
    raw: Mapping[str, Any],
) -> FamilyContribution:
    return FamilyContribution(
        family=family,
        signals=int(raw.get("signals", 0)),
        closed_trades=int(raw.get("closed_trades", 0)),
        wins=int(raw.get("wins", 0)),
        realized_pnl_usdt=float(raw.get("realized_pnl_usdt", 0.0)),
    )


def _invalid(
    reason: str,
    *,
    suite: str,
    contributions: tuple[FamilyContribution, ...] = (),
) -> AblationDecision:
    return AblationDecision(
        selected=False,
        reason=reason,
        suite=suite,
        family_mode=None,
        retained_family=None,
        removed_family=None,
        contributions=contributions,
    )


def select_single_family_ablation(
    summary: Mapping[str, Any],
) -> AblationDecision:
    """Select one diagnostic family removal without a parameter or asset search."""

    suite = str(summary.get("suite", "unknown"))
    if suite not in {"first", "screen"}:
        return _invalid("INVALID_OR_UNKNOWN_SUITE", suite=suite)
    if str(summary.get("implementation_revision")) != IMPLEMENTATION_REVISION:
        return _invalid("IMPLEMENTATION_REVISION_NOT_EXACT", suite=suite)
    if str(summary.get("auction_family_mode")) != "both":
        return _invalid("BASE_DID_NOT_INCLUDE_BOTH_FAMILIES", suite=suite)
    if bool(summary.get("diagnostic_family_ablation", False)):
        return _invalid("NESTED_ABLATION_FORBIDDEN", suite=suite)
    if not bool(summary.get("scenario_attribution_passed", False)):
        return _invalid("SCENARIO_ATTRIBUTION_INCOMPLETE", suite=suite)
    if bool(summary.get("suite_gate_passed", False)):
        return _invalid("BASE_GATE_ALREADY_PASSED", suite=suite)

    raw_families = summary.get("scenario_family_results", {})
    if not isinstance(raw_families, Mapping):
        return _invalid("SCENARIO_FAMILY_RESULTS_MISSING", suite=suite)
    expected = {INITIATIVE_FAMILY, FAILED_AUCTION_FAMILY}
    if set(raw_families) != expected:
        return _invalid("SCENARIO_FAMILY_SET_NOT_EXACT", suite=suite)

    contributions = tuple(
        _contribution(family, raw_families[family])
        for family in (INITIATIVE_FAMILY, FAILED_AUCTION_FAMILY)
    )
    reported_closed = int(summary.get("closed_trades", 0))
    if sum(item.closed_trades for item in contributions) != reported_closed:
        return _invalid(
            "FAMILY_CLOSED_TRADE_COUNT_MISMATCH",
            suite=suite,
            contributions=contributions,
        )

    positive = [
        item
        for item in contributions
        if item.closed_trades >= 1 and item.realized_pnl_usdt > 0.0
    ]
    negative = [
        item
        for item in contributions
        if item.closed_trades >= 1 and item.realized_pnl_usdt < 0.0
    ]
    if len(positive) != 1 or len(negative) != 1:
        if all(item.closed_trades == 0 for item in contributions):
            reason = "NO_EXECUTED_FAMILY_OPPORTUNITY"
        elif len(negative) == 2:
            reason = "BOTH_FAMILIES_ECONOMICALLY_NEGATIVE"
        elif len(positive) == 2:
            reason = "NO_DESTRUCTIVE_FAMILY_TO_ABLATE"
        elif any(item.closed_trades == 0 for item in contributions):
            reason = "SURVIVING_FAMILY_NOT_INDEPENDENTLY_EXECUTED"
        else:
            reason = "FAMILY_CONTRIBUTIONS_NOT_CLEANLY_SEPARATED"
        return _invalid(reason, suite=suite, contributions=contributions)

    retained = positive[0]
    removed = negative[0]
    return AblationDecision(
        selected=True,
        reason="ONE_POSITIVE_AND_ONE_NEGATIVE_FAMILY",
        suite=suite,
        family_mode=FAMILY_TO_MODE[retained.family],
        retained_family=retained.family,
        removed_family=removed.family,
        contributions=contributions,
    )


def _write_json(path: Path, decision: AblationDecision) -> None:
    payload = asdict(decision)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, default=None)
    args = parser.parse_args()

    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    decision = select_single_family_ablation(summary)
    _write_json(args.output, decision)
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"selected={'true' if decision.selected else 'false'}\n")
            stream.write(f"reason={decision.reason}\n")
            stream.write(f"suite={decision.suite}\n")
            stream.write(f"family_mode={decision.family_mode or ''}\n")
            stream.write(f"retained_family={decision.retained_family or ''}\n")
            stream.write(f"removed_family={decision.removed_family or ''}\n")
    print(json.dumps(asdict(decision), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
