#!/usr/bin/env python3
"""Bind a matrix-approved IRX variant to frozen dates before data access."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess


def main() -> None:
    root = Path(__file__).resolve().parent
    protocol_path = root / "irx_holdout_protocol.json"
    summary_path = root / "results" / "IRX_MATRIX" / "summary.json"
    if not protocol_path.is_file() or not summary_path.is_file():
        print("protocol or matrix summary not yet available")
        return
    protocol_bytes = protocol_path.read_bytes()
    summary_bytes = summary_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    summary = json.loads(summary_bytes)
    selected = summary.get("selected_variant")
    if selected not in {"STRICT", "BALANCED", "LEADERSHIP_DOMINANT"}:
        print("matrix produced no holdout-eligible IRX variant")
        return
    record = next((item for item in summary.get("records", []) if item.get("variant") == selected), None)
    if not isinstance(record, dict) or record.get("eligible") is not True:
        raise SystemExit("selected IRX variant is not matrix eligible")
    binding = {
        "schema": "candidate-11-irx-holdout-binding-v1",
        "selected_variant": selected,
        "frozen_weeks": protocol["weeks"],
        "protocol_sha256": sha256(protocol_bytes).hexdigest(),
        "matrix_summary_sha256": sha256(summary_bytes).hexdigest(),
        "source_commit_before_holdout_data": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip(),
        "eligibility_record": {
            key: record.get(key)
            for key in (
                "safety_passed", "losses_all_three_weeks",
                "diagnostic_closed_trades", "diagnostic_internal_plans",
                "diagnostic_log_growth", "w8_preserved",
            )
        },
        "market_data_opened": False,
        "success_claim": False,
    }
    path = root / "irx_holdout_candidate.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        immutable = {key: existing.get(key) for key in ("selected_variant", "frozen_weeks", "protocol_sha256", "matrix_summary_sha256")}
        proposed = {key: binding.get(key) for key in immutable}
        if immutable != proposed:
            raise SystemExit("frozen IRX candidate binding changed")
        print("IRX holdout candidate already bound")
        return
    path.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(binding, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
