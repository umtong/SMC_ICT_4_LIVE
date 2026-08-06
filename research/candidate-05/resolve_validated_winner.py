#!/usr/bin/env python3
"""Resolve one authoritative BTC winner from exact-control evidence only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


KNOWN_STRATEGIES = {
    "strategy_v26:ScenarioValidEntryStrategy",
    "strategy_v26_no_early_sponsored_ablation:NoEarlySponsoredParticipationStrategy",
    "strategy_v29b_external_displacement_fvg:ExternalDisplacementFvgStrategyV2",
    "strategy_v30_external_acceptance_retest:ExternalAcceptanceFirstRetestStrategy",
    "strategy_v31_impact_resiliency_reversal:ImpactResiliencyReversalStrategy",
    "strategy_v32_queue_pressure_release:QueuePressureReleaseStrategy",
}


def load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def resolve(evidence_root: Path) -> dict[str, Any]:
    sources = {
        "post_audit_continuation": load(evidence_root / "post_audit_continuation.json"),
        "v32_exact_control": load(evidence_root / "v32_queue_pressure_controlled.json"),
        "master_winner_audit": load(evidence_root / "master_winner_control_audit.json"),
        "master": load(evidence_root / "master_research_continuation.json"),
    }
    candidates: list[dict[str, Any]] = []

    post = sources["post_audit_continuation"]
    if post and post.get("classification") == "BTC_91D_ALPHA_GATE_PASSED":
        candidates.append({
            "priority": 1,
            "source": "post_audit_continuation.json",
            "winner": post.get("winner"),
            "classification": post.get("classification"),
        })

    v32 = sources["v32_exact_control"]
    if v32 and v32.get("classification") == "BTC_91D_ALPHA_GATE_PASSED":
        candidates.append({
            "priority": 2,
            "source": "v32_queue_pressure_controlled.json",
            "winner": v32.get("winner"),
            "classification": v32.get("classification"),
        })

    audit = sources["master_winner_audit"]
    if audit and audit.get("master_winner_validated") is True:
        candidates.append({
            "priority": 3,
            "source": "master_winner_control_audit.json",
            "winner": audit.get("winner") or audit.get("selection", {}).get("master_winner"),
            "classification": audit.get("classification"),
        })

    master = sources["master"]
    if master and master.get("classification") == "BTC_91D_ALPHA_GATE_PASSED":
        winner = master.get("winner")
        # Baseline-family winners do not add an incremental branch and therefore
        # do not require the branch-removal audit used for v29b-v31.
        if winner in {
            "strategy_v26:ScenarioValidEntryStrategy",
            "strategy_v26_no_early_sponsored_ablation:NoEarlySponsoredParticipationStrategy",
        }:
            candidates.append({
                "priority": 4,
                "source": "master_research_continuation.json",
                "winner": winner,
                "classification": master.get("classification"),
            })

    candidates = [item for item in candidates if item.get("winner") in KNOWN_STRATEGIES]
    candidates.sort(key=lambda item: item["priority"])
    selected = candidates[0] if candidates else None
    if selected:
        return {
            "schema": "candidate-05-validated-btc-winner-v1",
            "classification": "VALIDATED_BTC_WINNER_RESOLVED",
            "winner": selected["winner"],
            "source_evidence": selected["source"],
            "source_classification": selected["classification"],
            "all_valid_candidates": candidates,
            "next_action": "Run the real one-account four-symbol NautilusTrader competition with one global executable intent or position.",
        }
    return {
        "schema": "candidate-05-validated-btc-winner-v1",
        "classification": "NO_VALIDATED_BTC_WINNER",
        "winner": None,
        "source_evidence": None,
        "all_valid_candidates": [],
        "available_source_classifications": {
            key: None if value is None else value.get("classification")
            for key, value in sources.items()
        },
        "next_action": "Continue exact-control alpha research or repair the latest implementation error; do not start shared-account validation.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = resolve(args.evidence_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
