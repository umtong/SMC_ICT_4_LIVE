#!/usr/bin/env python3
"""Route decision-v3 without opening unauthorized market data."""
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


def trigger(
    *,
    action: str,
    decision_digest: str,
    protocol_name: str,
    target_name: str,
    extra: dict[str, object] | None = None,
) -> str:
    protocol = ROOT / protocol_name
    if not protocol.is_file():
        raise SystemExit(f"required frozen protocol missing: {protocol_name}")
    payload: dict[str, object] = {
        "action": action,
        "decision_sha256": decision_digest,
        "protocol_sha256": sha256(protocol.read_bytes()).hexdigest(),
    }
    if extra:
        payload.update(extra)
    target = ROOT / target_name
    write(target, payload)
    return target.name


def main() -> None:
    decision_path = ROOT / "results" / "RESEARCH_DECISION.json"
    if not decision_path.is_file():
        print("decision unavailable")
        return
    raw = decision_path.read_bytes()
    decision = json.loads(raw)
    action = str(decision.get("next_action") or "")
    digest = sha256(raw).hexdigest()
    created: list[str] = []

    if action == "RUN_FROZEN_BALANCE_ACCEPTANCE_WEEKS":
        created.append(trigger(
            action=action,
            decision_digest=digest,
            protocol_name="microstructure_v3_protocol.json",
            target_name="microstructure_v3_run_trigger.txt",
        ))
    elif action == "RUN_FROZEN_CROSS_MARKET_WEEKS":
        check_path = ROOT / "results" / "CROSS_MARKET_CHECK" / "status.json"
        if not check_path.is_file():
            raise SystemExit("cross-market implementation check missing")
        check = json.loads(check_path.read_text(encoding="utf-8"))
        if check.get("passed") is not True:
            raise SystemExit("cross-market implementation check did not pass")
        created.append(trigger(
            action=action,
            decision_digest=digest,
            protocol_name="cross_market_protocol.json",
            target_name="cross_market_family_trigger.json",
            extra={"implementation_check_sha256": sha256(check_path.read_bytes()).hexdigest()},
        ))
    elif action == "FREEZE_CROSS_MARKET_UNTOUCHED_HOLDOUTS":
        target = ROOT / "cross_market_holdout_freeze_trigger.json"
        if write(target, {
            "action": action,
            "decision_sha256": digest,
            "market_data_opened": False,
            "success_claim": False,
        }):
            created.append(target.name)
    elif action == "OPEN_CAUSAL_VOLATILITY_STATE_ROUTER_FAMILY":
        target = ROOT / "volatility_state_family_trigger.json"
        if write(target, {
            "action": action,
            "decision_sha256": digest,
            "market_data_opened": False,
            "success_claim": False,
        }):
            created.append(target.name)
    else:
        target = ROOT / "results" / "NEXT_ACTION.json"
        if write(target, {
            "action": action,
            "decision_sha256": digest,
            "research_trigger_created": False,
            "success_claim": False,
        }):
            created.append(str(target.relative_to(ROOT)))
    print(json.dumps({"action": action, "created": created}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
