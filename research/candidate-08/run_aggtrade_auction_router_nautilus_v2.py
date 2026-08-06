"""Execute the source-stable auction router with failed-auction sweep refinement v2."""

from __future__ import annotations

import run_aggtrade_auction_router_nautilus as runner
from aggtrade_auction_router_signals_v2 import build_auction_router_signals


# ``runner._build_router_signals`` resolves this module global at call time. Rebinding only the
# detector implementation preserves its already-verified execution, risk, funding, liquidation,
# family-ablation, and reporting contracts.
runner.build_auction_router_signals = build_auction_router_signals
runner.base_runner.build_acceptance_signals = runner._build_router_signals


if __name__ == "__main__":
    raise SystemExit(runner.base_runner.main())
