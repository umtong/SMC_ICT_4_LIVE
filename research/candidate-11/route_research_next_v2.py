#!/usr/bin/env python3
"""Route Candidate 11 decision-v2 without opening unauthorized market data."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def write(path: Path, payload: dict[str, object]) -> bool:
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def main() -> None:
    decision_path = ROOT / "results" / "RESEARCH_DECISION.json"
    if not decision_path.is_file():
        print("decision unavailable")
        return
    raw = decision_path.read_bytes()
    decision = json.loads(raw)
    action = decision.get("next_action")
    digest = sha256(raw).hexdigest()
    created: list[str] = []

    if action == "RUN_FROZEN_BALANCE_ACCEPTANCE_WEEKS":
        protocol = ROOT / "microstructure_v3_protocol.json"
        if not protocol.is_file():
            raise SystemExit("balance-acceptance protocol not frozen")
        path = ROOT / "microstructure_v3_run_trigger.txt"
        if write(path, {
            "action": action,
            "decision_sha256": digest,
            "protocol_sha256": sha256(protocol.read_bytes()).hexdigest(),
        }):
            created.append(path.name)
    elif action == "REPLACE_BTC_MICROSTRUCTURE_SUITE_WITH_CROSS_MARKET_CAUSAL_LEADER_FAMILY":
        path = ROOT / "cross_market_family_trigger.json"
        if write(path, {
            "action": action,
            "decision_sha256": digest,
            "market_data_opened": False,
            "success_claim": False,
        }):
            created.append(path.name)
    else:
        path = ROOT / "results" / "NEXT_ACTION.json"
        if write(path, {
            "action": action,
            "decision_sha256": digest,
            "research_trigger_created": False,
            "success_claim": False,
        }):
            created.append(str(path.relative_to(ROOT)))
    print(json.dumps({"action": action, "created": created}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
