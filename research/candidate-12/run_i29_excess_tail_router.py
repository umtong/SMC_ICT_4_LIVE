#!/usr/bin/env python3
"""Materialize Candidate 12 I29 over the frozen I27 continuous-account runner.

I29 preserves the exact I19 alpha engines, NautilusTrader execution, current-NAV
risk sizing, synchronized four-market observation, and one-global-slot account.
It adds the completed-auction profile router plus the structural excess-tail
clearance transition before global arbitration.
"""
from __future__ import annotations

from pathlib import Path


def _replace(
    source: str,
    old: str,
    new: str,
    *,
    label: str,
    expected: int = 1,
) -> str:
    count = source.count(old)
    if count != expected:
        raise RuntimeError(
            f"I27 source drifted at {label}: expected {expected} occurrence(s), found {count}"
        )
    return source.replace(old, new)


def materialize(source: str) -> str:
    source = _replace(
        source,
        "from metrics import decimal_value\n\n"
        "from smc_ict_4.event_log import EventLogError, write_events",
        "from metrics import decimal_value\n"
        "from auction_profile_router_i29 import ExcessTailAuctionRouter\n\n"
        "from smc_ict_4.event_log import EventLogError, write_events",
        label="router-import",
    )
    source = _replace(
        source,
        '        "candidate": "candidate-12-i19-four-market-global-slot-portfolio",',
        '        "candidate": "candidate-12-i29-excess-tail-auction-router",',
        label="candidate-label",
    )
    source = _replace(
        source,
        '            "schema": "candidate-12-i27-portfolio-source-manifest-v1",',
        '            "schema": "candidate-12-i29-excess-tail-source-manifest-v1",',
        label="manifest-schema",
    )
    source = _replace(
        source,
        '    all_bars.sort(key=lambda bar: (int(bar.ts_init), str(bar.bar_type)))\n\n'
        '    starting_nav = Decimal(account["starting_nav"])',
        '    all_bars.sort(key=lambda bar: (int(bar.ts_init), str(bar.bar_type)))\n'
        '    auction_router = ExcessTailAuctionRouter(\n'
        '        frames,\n'
        '        {\n'
        '            symbol: float(config["symbols"][symbol]["price_increment"])\n'
        '            for symbol in SYMBOLS\n'
        '        },\n'
        '    )\n\n'
        '    starting_nav = Decimal(account["starting_nav"])',
        label="router-construction",
    )
    source = _replace(
        source,
        '            self.boundary_actions_started = False\n',
        '            self.boundary_actions_started = False\n'
        '            self.auction_router = auction_router\n',
        label="strategy-router",
    )
    source = _replace(
        source,
        '''                if plan is not None:
                    candidates.append(RankedPlan(symbol=symbol, plan=plan))''',
        '''                if plan is not None:
                    router_decision = self.auction_router.evaluate(symbol, plan)
                    plan.details["auction_profile_router"] = router_decision.context
                    if not router_decision.approved:
                        self.logic[symbol].mark_plan_rejected(
                            plan,
                            ts_ns,
                            router_decision.reason,
                            router_decision.context,
                        )
                        self.rejections.append(
                            {
                                "type": "AUCTION_PROFILE_ROUTED_REJECTED",
                                "observed_ts_ns": plan.observed_ts_ns,
                                "scenario_id": plan.scenario_id,
                                "symbol": symbol,
                                "reason": router_decision.reason,
                                "net_r": plan.net_r,
                                "context": router_decision.context,
                            }
                        )
                        continue
                    candidates.append(RankedPlan(symbol=symbol, plan=plan))''',
        label="pre-arbitration-router",
    )
    source = _replace(
        source,
        '                f"candidate-12-i27-portfolio-{week_id.lower()}-"',
        '                f"candidate-12-i29-excess-tail-{week_id.lower()}-"',
        label="run-id",
    )
    source = _replace(
        source,
        '        default=ROOT / "results" / "I27-PORTFOLIO-W1",',
        '        default=ROOT / "results" / "I29-EXCESS-TAIL-W1",',
        label="default-output",
    )
    if source.count("AUCTION_PROFILE_ROUTED_REJECTED") != 1:
        raise RuntimeError("I29 router was not materialized exactly once")
    return source


_BASE = Path(__file__).resolve().with_name("run_i27_portfolio.py")
_SOURCE = materialize(_BASE.read_text(encoding="utf-8"))
exec(compile(_SOURCE, str(_BASE), "exec"), globals(), globals())
