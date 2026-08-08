#!/usr/bin/env python3
"""Create a trigger only for the next family authorized by committed evidence."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def write_if_changed(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def main() -> None:
    decision_path = ROOT / "results" / "RESEARCH_DECISION.json"
    if not decision_path.is_file():
        print("research decision unavailable; no route created")
        return
    payload = decision_path.read_bytes()
    decision = json.loads(payload)
    action = decision.get("next_action")
    digest = sha256(payload).hexdigest()
    created: list[str] = []
    if action == "OPEN_MULTI_HORIZON_IMPACT_CONTINUATION_FAMILY":
        protocol = ROOT / "microstructure_v3_protocol.json"
        if not protocol.is_file():
            print("balance-acceptance protocol is not yet frozen")
            return
        target = ROOT / "microstructure_v3_run_trigger.txt"
        content = json.dumps({
            "decision_sha256": digest,
            "action": action,
            "protocol_sha256": sha256(protocol.read_bytes()).hexdigest(),
        }, indent=2, sort_keys=True) + "\n"
        if write_if_changed(target, content):
            created.append(target.name)
    else:
        marker = ROOT / "results" / "NEXT_ACTION.json"
        content = json.dumps({
            "decision_sha256": digest,
            "action": action,
            "research_trigger_created": False,
            "success_claim": False,
        }, indent=2, sort_keys=True) + "\n"
        if write_if_changed(marker, content):
            created.append(str(marker.relative_to(ROOT)))
    print(json.dumps({"action": action, "created": created}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
