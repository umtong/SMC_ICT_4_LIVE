#!/usr/bin/env python3
"""Idempotently insert the FATR depth gate into Nautilus execution."""

from __future__ import annotations

from pathlib import Path


IMPORT_ANCHOR = "from failed_acceptance_trap import build_failed_acceptance_trap\n"
IMPORT_INSERTION = IMPORT_ANCHOR + "from synchronous_depth_gate import evaluate_failed_acceptance_depth\n"

BLOCK_ANCHOR = '''                if trap_signal is not None:
                    signal = trap_signal
                    trap_armed = True
                    trap_counts["armed"] = int(trap_counts.get("armed", 0)) + 1
                    confirmation_details.update(
                        {
                            "failed_defense_action": action,
                            "trap_armed": True,
                            "trap_direction": trap_signal.direction,
                            "trap_stop_price": trap_signal.stop_price,
                            "trap_target_price": trap_signal.target_price,
                            "trap_target_reason": trap_signal.target_reason,
                        },
                    )
                else:
'''

BLOCK_INSERTION = '''                if trap_signal is not None:
                    signal = trap_signal
                    depth_required = bool(
                        self._logic_params.get("fatr_require_depth_confirmation", False)
                    )
                    depth_passed = True
                    depth_result = None
                    if depth_required:
                        depth_result = evaluate_failed_acceptance_depth(
                            original_signal,
                            trap_signal,
                            snapshot,
                            self._logic_params,
                        )
                        depth_passed = bool(depth_result.passed)
                        depth_counts = self.diagnostics.setdefault(
                            "failed_acceptance_depth_gate_counts",
                            {"passed": 0, "failed": 0},
                        )
                        depth_key = "passed" if depth_passed else "failed"
                        depth_counts[depth_key] = int(depth_counts.get(depth_key, 0)) + 1
                        self.diagnostics.setdefault("failed_acceptance_depth_candidates", []).append(
                            {
                                "scenario_id": original_signal.scenario_id,
                                "direction": trap_signal.direction,
                                "passed": depth_passed,
                                "reason": depth_result.reason,
                                **dict(depth_result.metrics),
                            },
                        )
                    confirmation_details.update(
                        {
                            "failed_defense_action": action,
                            "depth_confirmation_required": depth_required,
                            "depth_confirmation_passed": depth_passed,
                            "depth_confirmation_reason": (
                                None if depth_result is None else depth_result.reason
                            ),
                            "trap_direction": trap_signal.direction,
                            "trap_stop_price": trap_signal.stop_price,
                            "trap_target_price": trap_signal.target_price,
                            "trap_target_reason": trap_signal.target_reason,
                        },
                    )
                    if depth_passed:
                        trap_armed = True
                        trap_counts["armed"] = int(trap_counts.get("armed", 0)) + 1
                        confirmation_details["trap_armed"] = True
                    else:
                        trap_counts["not_armed"] = int(trap_counts.get("not_armed", 0)) + 1
                        reason = "FAILED_ACCEPTANCE_DEPTH_RESILIENCY_NOT_CONFIRMED"
                        confirmation_details["trap_armed"] = False
                else:
'''


def main() -> int:
    path = Path(__file__).resolve().with_name("nautilus_execution.py")
    text = path.read_text(encoding="utf-8")

    if "from synchronous_depth_gate import evaluate_failed_acceptance_depth" not in text:
        if IMPORT_ANCHOR not in text:
            raise RuntimeError("failed-acceptance import anchor changed; refusing ambiguous patch")
        text = text.replace(IMPORT_ANCHOR, IMPORT_INSERTION, 1)

    if '"failed_acceptance_depth_gate_counts"' not in text:
        if BLOCK_ANCHOR not in text:
            raise RuntimeError("failed-acceptance execution anchor changed; refusing ambiguous patch")
        text = text.replace(BLOCK_ANCHOR, BLOCK_INSERTION, 1)

    path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
