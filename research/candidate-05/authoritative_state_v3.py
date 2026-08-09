#!/usr/bin/env python3
"""Resolve final authority with independent lifecycle audit precedence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from authoritative_state_v2 import markdown as markdown_v2
from authoritative_state_v2 import resolve as resolve_v2


def load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def resolve(evidence_root: Path) -> dict[str, Any]:
    state = resolve_v2(evidence_root)
    audit = load(evidence_root / "final_evidence_audit.json")
    if audit is None:
        state["schema"] = "candidate-05-authoritative-state-v3"
        state["independent_final_audit"] = None
        state["audited_project_goal_passed"] = False
        return state

    state["schema"] = "candidate-05-authoritative-state-v3"
    state["independent_final_audit"] = audit
    audited_pass = bool(audit.get("audited_project_goal_passed", False))
    state["audited_project_goal_passed"] = audited_pass

    audit_classification = str(audit.get("classification", ""))
    if audited_pass:
        state["source_evidence"] = "final_evidence_audit.json"
        state["classification"] = audit_classification
        state["project_goal_passed"] = True
        state["implementation_or_evidence_error"] = False
        state["logic_or_robustness_failure"] = False
        state["selected_strategy"] = audit.get("selected_strategy")
        state["next_action"] = audit.get("next_action")
    elif audit_classification.startswith("IMPLEMENTATION") or audit_classification.startswith("EVIDENCE_ERROR"):
        state["source_evidence"] = "final_evidence_audit.json"
        state["classification"] = audit_classification
        state["project_goal_passed"] = False
        state["implementation_or_evidence_error"] = True
        state["logic_or_robustness_failure"] = False
        state["selected_strategy"] = None
        state["next_action"] = audit.get("next_action")
    else:
        # A non-passing audit does not replace a more specific end-to-end logic
        # classification, but it prevents any unaudited project-pass claim.
        state["project_goal_passed"] = False
        state["selected_strategy"] = None
    return state


def markdown(state: dict[str, Any]) -> str:
    base = markdown_v2(state).rstrip()
    audit = state.get("independent_final_audit")
    lines = [base, "", "## Independent final audit", ""]
    if isinstance(audit, dict):
        lines.extend(
            [
                f"- Classification: `{audit.get('classification')}`",
                f"- Audited project pass: `{audit.get('audited_project_goal_passed')}`",
                f"- Lifecycle audit pass: `{audit.get('lifecycle_audit_pass')}`",
            ],
        )
    else:
        lines.append("- Not yet available.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    state = resolve(args.evidence_root.resolve())
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(state), encoding="utf-8")
    print(json.dumps(state, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
