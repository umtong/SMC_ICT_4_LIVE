"""V3 production entrypoint with explicit zero-trade path-revision evidence.

This module fixes an evidence-only boundary in the V3 flow-response runner.  The underlying V3
wrapper correctly regards a zero-trade run as having complete path evidence, because there are no
closed trades whose post-run path can be missing.  It nevertheless serialized the per-revision
counter as an empty object.  The staged V3 evidence contract requires an explicit
``{revision: 0}`` count so a clean no-opportunity result cannot be confused with a missing diagnostic
implementation.

No detector state, threshold, target, stop, order, fill, account, funding, liquidation, position
size, or promotion rule is changed here.  NautilusTrader remains the sole execution and account
engine through the already-verified base runner.
"""

from __future__ import annotations

from typing import Any, Mapping

import run_aggtrade_flow_response_auction_nautilus as base
from flow_response_trade_path_diagnostics_v2 import DIAGNOSTIC_REVISION


EVIDENCE_WRAPPER_REVISION = "EXPLICIT_ZERO_TRADE_PATH_REVISION_COUNT_V4"
_ORIGINAL_SUITE_SUMMARY = base._flow_response_suite_summary


def _flow_response_suite_summary_v4(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = _ORIGINAL_SUITE_SUMMARY(config, suite, results)
    closed_trades = int(summary.get("closed_trades", 0))
    raw_path_summary = summary.get("trade_path_diagnostic_summary", {})
    if not isinstance(raw_path_summary, Mapping):
        raise RuntimeError("V3 path diagnostic summary was not a mapping")

    path_summary = dict(raw_path_summary)
    revision_counts = dict(path_summary.get("diagnostic_revision_counts", {}))
    if closed_trades == 0:
        revision_counts = {DIAGNOSTIC_REVISION: 0}
    path_summary["diagnostic_revision_counts"] = revision_counts
    path_summary["expected_diagnostic_revision"] = DIAGNOSTIC_REVISION
    summary["trade_path_diagnostic_summary"] = path_summary
    summary["evidence_wrapper_revision"] = EVIDENCE_WRAPPER_REVISION

    path_complete = (
        int(path_summary.get("records", -1)) == closed_trades
        and int(path_summary.get("complete_records", -1)) == closed_trades
        and revision_counts == {DIAGNOSTIC_REVISION: closed_trades}
    )
    checks = summary.setdefault("suite_gate_checks", {})
    checks["complete_post_run_trade_path_diagnostics"] = path_complete
    summary["suite_gate_passed"] = bool(
        summary.get("suite_gate_passed", False) and path_complete
    )
    return summary


base._flow_response_suite_summary = _flow_response_suite_summary_v4
base.runner.base_runner._suite_summary = _flow_response_suite_summary_v4


if __name__ == "__main__":
    raise SystemExit(base.runner.base_runner.main())
