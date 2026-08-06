"""Execute the source-stable auction router with failed-auction sweep refinement v2."""

from __future__ import annotations

from typing import Any, Mapping

import run_aggtrade_auction_router_nautilus as runner
from aggtrade_auction_router_signals_v2 import (
    IMPLEMENTATION_REVISION,
    build_auction_router_signals,
)


# ``runner._build_router_signals`` resolves this module global at call time. Rebinding only the
# detector implementation preserves its already-verified execution, risk, funding, liquidation,
# family-ablation, and reporting contracts.
runner.build_auction_router_signals = build_auction_router_signals

_original_suite_summary = runner._auction_suite_summary


def _v2_suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = _original_suite_summary(config, suite, results)
    summary["implementation_revision"] = IMPLEMENTATION_REVISION
    return summary


runner._auction_suite_summary = _v2_suite_summary
runner.base_runner.build_acceptance_signals = runner._build_router_signals
runner.base_runner._suite_summary = _v2_suite_summary


if __name__ == "__main__":
    raise SystemExit(runner.base_runner.main())
