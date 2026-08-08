#!/usr/bin/env python3
"""Materialize exact Candidate-13 v4 source plus Candidate-02 V158 OI routing.

Candidate 13's v4 protocol was committed after the byte-frozen strategy files.
The protocol therefore names the earlier strategy freeze while the complete
runnable snapshot (protocol and wrappers included) lives at a later commit.
This materializer keeps those concepts separate and verifies every pre-existing
locked blob before it adds the V158 files.
"""
from __future__ import annotations

import argparse
from hashlib import sha1, sha256
import json
from pathlib import Path
import shutil
from typing import Any

V158_FILES = (
    "v158_oi_router.py",
    "v158_run_leadership_scdam.py",
    "v158_candidate13_runner.py",
)


def git_blob_oid(payload: bytes) -> str:
    return sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def verify_candidate13_locked_blobs(
    root: Path,
    protocol: dict[str, Any],
) -> dict[str, str]:
    locked = protocol.get("locked_source")
    if not isinstance(locked, dict):
        raise TypeError("protocol.locked_source must be an object")
    blobs = locked.get("blobs")
    if not isinstance(blobs, dict) or not blobs:
        raise TypeError("protocol.locked_source.blobs must be a non-empty object")
    verified: dict[str, str] = {}
    mismatches: list[str] = []
    for name, expected in sorted(blobs.items()):
        path = root / str(name)
        if not path.is_file():
            mismatches.append(f"{name}: missing")
            continue
        actual = git_blob_oid(path.read_bytes())
        if actual != str(expected):
            mismatches.append(f"{name}: expected {expected}, actual {actual}")
        else:
            verified[str(name)] = actual
    if mismatches:
        raise RuntimeError(
            "Candidate13 v4 snapshot violates its pre-existing frozen blob contract:\n"
            + "\n".join(mismatches)
        )
    return verified


def materialize(
    root: Path,
    *,
    source_snapshot_commit: str,
    strategy_freeze_commit: str,
) -> dict[str, Any]:
    source = Path(__file__).resolve().parent
    protocol_path = root / "protocol-v4-regression.json"
    protocol = load_object(protocol_path)

    protocol_freeze = str(protocol["locked_source"]["strategy_freeze_commit"])
    if protocol_freeze != strategy_freeze_commit:
        raise RuntimeError(
            "Candidate13 v4 strategy-freeze mismatch: "
            f"protocol={protocol_freeze}, requested={strategy_freeze_commit}"
        )
    verified_candidate13_blobs = verify_candidate13_locked_blobs(root, protocol)

    for name in V158_FILES:
        origin = source / name
        if not origin.is_file():
            raise FileNotFoundError(origin)
        shutil.copy2(origin, root / name)

    protocol["schema"] = "candidate-02-v158-candidate13-v4-oi-reset-development-v1"
    protocol["candidate"] = "candidate-02-v158-candidate13-v4-oi-reset-router"
    protocol["created_utc"] = "2026-08-09"
    protocol["claim_eligible"] = False
    protocol["success_claim"] = False
    protocol["research_question"] = (
        "Does Candidate13 v4 preserve its broad causal opportunity set when FAR is "
        "restricted by the pre-existing Candidate05 positioning-reset state, while "
        "AAC, execution, costs, current-NAV 3% risk and the global one-slot account "
        "remain unchanged?"
    )
    protocol["hypothesis"] = {
        "context": "Candidate13 v4 dynamic price-discovery and early-auction transfer",
        "far_state": (
            "approve only with an official post-sweep metric visible at confirmation "
            "and causal 15-minute OI change <= +0.10%"
        ),
        "aac_state": "unchanged Candidate13 v4 true-acceptance continuation",
        "missing_data": "fail closed; no synthetic OI value",
        "threshold_origin": (
            "Candidate05 positioning-reset predicate defined before Candidate13 V13 OI diagnostics"
        ),
    }
    protocol["locked_source"]["origin_branch"] = "research/candidate-13"
    # Preserve the protocol's byte-level strategy freeze. The later snapshot is
    # provenance for the complete runnable package, not a new strategy freeze.
    protocol["locked_source"]["strategy_freeze_commit"] = strategy_freeze_commit
    protocol["locked_source"]["materialization_source_commit"] = source_snapshot_commit
    protocol["locked_source"]["enforce_git_blobs"] = True
    for name in V158_FILES:
        payload = (root / name).read_bytes()
        protocol["locked_source"]["blobs"][name] = git_blob_oid(payload)
    for record in protocol["selection"]["holdouts"].values():
        record["role"] = "exposed-development"
    protocol["selection"]["method"] = (
        "Reuse Candidate13 W10-W29 only as exposed regression data. The pre-existing "
        "OI rule may be rejected or qualify as a reusable high-precision component, "
        "but these intervals cannot support a final success claim."
    )
    # Keep the project target in the aggregate gate. A separate component gate is
    # evaluated by the workflow without redefining final success.
    protocol["aggregate_gate"].update({
        "observed_calendar_days": 140,
        "minimum_daily_geometric_growth": 0.01,
        "minimum_closed_trades": 25,
        "minimum_active_weeks": 12,
        "minimum_win_rate": 0.8,
        "minimum_payoff_ratio": 1.2,
        "maximum_trade_path_drawdown": 0.2,
        "maximum_positive_log_growth_share_from_one_week": 0.25,
        "risk_fraction": 0.03,
        "global_pending_entry_plus_position_limit": 1,
    })
    write_json(root / "v158_protocol.json", protocol)

    manifest = {
        "schema": "candidate-02-v158-materialization-v2",
        "candidate13_materialization_source_commit": source_snapshot_commit,
        "candidate13_strategy_freeze_commit": strategy_freeze_commit,
        "candidate13_protocol": "protocol-v4-regression.json",
        "candidate13_locked_blobs_verified_before_v158": verified_candidate13_blobs,
        "v158_files": {
            name: {
                "git_blob": git_blob_oid((root / name).read_bytes()),
                "sha256": sha256((root / name).read_bytes()).hexdigest(),
            }
            for name in V158_FILES
        },
        "only_market_logic_change": (
            "FAR requires post-sweep visible official OI metric and oi_change_15m <= 0.001"
        ),
        "unchanged": [
            "Candidate13 v4 price-discovery leadership",
            "Candidate13 v4 AAC",
            "local auction geometry",
            "entry and invalidation",
            "natural target",
            "NautilusTrader execution",
            "fees, slippage and margin",
            "current-NAV 3% planned loss",
            "global one pending-entry-or-position slot",
        ],
        "development_only": True,
        "success_claim_allowed": False,
    }
    write_json(root / "v158_materialization.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-snapshot-commit", required=True)
    parser.add_argument("--strategy-freeze-commit", required=True)
    args = parser.parse_args()
    result = materialize(
        args.root.resolve(),
        source_snapshot_commit=args.source_snapshot_commit,
        strategy_freeze_commit=args.strategy_freeze_commit,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
