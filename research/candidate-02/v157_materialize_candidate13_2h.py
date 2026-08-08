#!/usr/bin/env python3
"""Materialize Candidate-02 V157 from the pinned Candidate-13 source.

The only alpha change is an additional causally completed two-hour auction
range in the external-liquidity registry. FAR/AAC classification, market
leadership, peer confirmation, entry, invalidation, target selection,
NautilusTrader execution, costs, global mutex, and 3% NAV risk remain byte-for-
byte inherited from Candidate 13.
"""
from __future__ import annotations

import argparse
from hashlib import sha1, sha256
import json
from pathlib import Path
from typing import Any


def git_blob_oid(payload: bytes) -> str:
    return sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _restore_locked_candidate13_runner(root: Path, protocol: dict[str, Any]) -> dict[str, str]:
    """Restore the exact runner blob named by the frozen Candidate-13 protocol.

    The pinned Candidate-13 commit stores a thin compatibility wrapper at
    ``run_leadership_scdam.py`` and the byte-exact frozen runner at
    ``run_leadership_scdam_base.py``. The protocol intentionally locks the
    executable runner under the former name. Replacing the wrapper with the
    verified base bytes is an implementation materialization repair, not an
    alpha or execution-policy change.
    """
    runner = root / "run_leadership_scdam.py"
    base = root / "run_leadership_scdam_base.py"
    expected = str(protocol["locked_source"]["blobs"]["run_leadership_scdam.py"])
    current_payload = runner.read_bytes()
    current = git_blob_oid(current_payload)
    if current == expected:
        return {
            "expected_git_blob": expected,
            "pre_restore_git_blob": current,
            "restored_from": "run_leadership_scdam.py",
        }
    if not base.is_file():
        raise RuntimeError(
            "Candidate-13 runner lock mismatch and run_leadership_scdam_base.py is missing"
        )
    base_payload = base.read_bytes()
    base_oid = git_blob_oid(base_payload)
    if base_oid != expected:
        raise RuntimeError(
            "Candidate-13 runner lock mismatch: "
            f"wrapper={current}, base={base_oid}, expected={expected}"
        )
    runner.write_bytes(base_payload)
    if git_blob_oid(runner.read_bytes()) != expected:
        raise AssertionError("failed to restore exact Candidate-13 runner bytes")
    return {
        "expected_git_blob": expected,
        "pre_restore_git_blob": current,
        "restored_from": "run_leadership_scdam_base.py",
    }


def materialize(root: Path, *, source_commit: str) -> dict[str, Any]:
    logic_path = root / "logic.py"
    protocol_path = root / "protocol.json"
    protocol = read_json(protocol_path)
    runner_restore = _restore_locked_candidate13_runner(root, protocol)
    logic = logic_path.read_text(encoding="utf-8")

    init_old = """        self._internal_agg = _TimeAggregator(config.internal_tf_bars)\n        self._context_agg = _TimeAggregator(config.external_tf_bars)\n        self._day_agg = _TimeAggregator(1440)\n"""
    init_new = """        self._internal_agg = _TimeAggregator(config.internal_tf_bars)\n        # V157 opportunity expansion: a completed two-hour auction is a\n        # weaker, shorter-lived external pool. It cannot emit a trade by\n        # itself; the inherited FAR/AAC state machine must still complete.\n        self._two_hour_agg = _TimeAggregator(120)\n        self._context_agg = _TimeAggregator(config.external_tf_bars)\n        self._day_agg = _TimeAggregator(1440)\n"""
    update_old = """        context = self._context_agg.update(bar)\n        if context is not None:\n            self.context_bars.append(context)\n            self._new_range(context, \"COMPLETED_4H_AUCTION\", 2, self.config.range_expiry_bars)\n        daily = self._day_agg.update(bar)\n"""
    update_new = """        two_hour = self._two_hour_agg.update(bar)\n        if two_hour is not None:\n            # Half the 4h-range lifetime preserves proportional market memory\n            # without fitting to the exposed intervals. Stronger 4h/daily\n            # pools still dominate duplicate levels through the inherited merge.\n            self._new_range(\n                two_hour,\n                \"COMPLETED_2H_AUCTION\",\n                1,\n                max(240, self.config.range_expiry_bars // 2),\n            )\n        context = self._context_agg.update(bar)\n        if context is not None:\n            self.context_bars.append(context)\n            self._new_range(context, \"COMPLETED_4H_AUCTION\", 2, self.config.range_expiry_bars)\n        daily = self._day_agg.update(bar)\n"""
    if logic.count(init_old) != 1 or logic.count(update_old) != 1:
        raise RuntimeError("pinned Candidate-13 logic does not match V157 patch contract")
    patched = logic.replace(init_old, init_new).replace(update_old, update_new)
    compile(patched, str(logic_path), "exec")
    logic_path.write_text(patched, encoding="utf-8")

    protocol["schema"] = "candidate-02-v157-candidate13-two-hour-pools-development-v1"
    protocol["candidate"] = "candidate-02-v157-price-discovery-with-two-hour-auctions"
    protocol["claim_eligible"] = False
    protocol["research_question"] = (
        "Can the unchanged Candidate-13 price-discovery FAR/AAC policy preserve "
        "its high accuracy while completed two-hour auction endpoints add enough "
        "independent external-liquidity opportunities to pass the activity gate?"
    )
    protocol["locked_source"]["origin_branch"] = "research/candidate-13"
    protocol["locked_source"]["strategy_freeze_commit"] = source_commit
    protocol["locked_source"]["enforce_git_blobs"] = True
    protocol["locked_source"]["blobs"]["logic.py"] = git_blob_oid(patched.encode("utf-8"))
    protocol["selection"]["method"] = (
        "Reuse Candidate-13 W10-W14 strictly as exposed development. They may "
        "reject V157 or authorize a later exact-source prospective freeze, but "
        "can never support a success claim."
    )
    for record in protocol["selection"]["holdouts"].values():
        record["role"] = "exposed-development"
    write_json(root / "v157_protocol.json", protocol)

    manifest = {
        "schema": "candidate-02-v157-materialization-v2",
        "candidate13_source_commit": source_commit,
        "logic_git_blob": git_blob_oid(patched.encode("utf-8")),
        "logic_sha256": sha256(patched.encode("utf-8")).hexdigest(),
        "runner_restore": runner_restore,
        "implementation_fix": (
            "restore exact protocol-locked Candidate13 base runner over its thin compatibility wrapper"
        ),
        "single_alpha_change": "add completed 2h auction endpoints as strength-1 external pools",
        "unchanged_components": [
            "dynamic price-discovery leader",
            "FAR/AAC state machine",
            "peer confirmation",
            "local entry geometry",
            "natural target selection",
            "NautilusTrader execution",
            "fees and margin",
            "3% current-NAV planned loss",
            "one global pending-entry-or-position slot"
        ],
        "development_only": True,
        "success_claim_allowed": False,
    }
    write_json(root / "v157_materialization.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.root.resolve(), source_commit=args.source_commit), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
