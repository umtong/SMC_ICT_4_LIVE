"""Run candidate-08 auction-router signals through the verified shared-account runner."""

from __future__ import annotations

import run_aggtrade_acceptance_nautilus as base_runner

from aggtrade_auction_router_signals import build_auction_router_signals


base_runner.build_acceptance_signals = build_auction_router_signals


if __name__ == "__main__":
    raise SystemExit(base_runner.main())
