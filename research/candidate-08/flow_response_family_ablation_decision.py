"""Predeclared single-family diagnostic decision for flow-response auction failures.

No family is selected merely because it is less bad. Exactly one diagnostic removal is permitted
only when the valid both-family evidence contains independent trades from both families, one family
is cost-after positive, and the other is cost-after negative. All other outcomes terminate this
candidate or require a new hypothesis rather than a fitted family choice.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from aggtrade_flow_response_auction_signals_v2 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
)


FAMILY_TO_MODE = {
    INITIATIVE_FAMILY: "initiative_only",
    ABSORPTION_FAMILY: "absorption_only",
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _contribution(family: str, raw: Mapping[str, Any]) -> FamilyContribution:
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


def select_single_family_ablation(summary: Mapping[str, Any]) -> AblationDecision:
    suite = str(summary.get("suite", "unknown"))
    if suite not in {"first", "screen"}:
        return _invalid("INVALID_OR_UNKNOWN_SUITE", suite=suite)
    if str(summary.get("implementation_revision")) != IMPLEMENTATION_REVISION:
        return _invalid("IMPLEMENTATION_REVISION_NOT_EXACT", suite=suite)
    mode = str(
        summary.get(
            "flow_response_family_mode",
            summary.get("auction_family_mode", ""),
        )
    )
    if mode != "both":
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
    expected = {INITIATIVE_FAMILY, ABSORPTION_FAMILY}
    if set(raw_families) != expected:
        return _invalid("SCENARIO_FAMILY_SET_NOT_EXACT", suite=suite)

    contributions = tuple(
        _contribution(family, raw_families[family])
        for family in (INITIATIVE_FAMILY, ABSORPTION_FAMILY)
    )
    if sum(item.closed_trades for item in contributions) != int(
        summary.get("closed_trades", 0)
    ):
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


__all__ = [
    "ABSORPTION_FAMILY",
    "AblationDecision",
    "FamilyContribution",
    "IMPLEMENTATION_REVISION",
    "INITIATIVE_FAMILY",
    "select_single_family_ablation",
]
