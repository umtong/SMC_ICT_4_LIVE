"""Insert Candidate 33 synchronized dislocation plans into the frozen runner."""
from __future__ import annotations

IMPORT_ANCHOR = "from session_engine import RegionalHandoffAuctionEngine\n"
IMPORT_REPLACEMENT = (
    "from session_engine import RegionalHandoffAuctionEngine\n"
    "from candidate33_cross_sectional_dislocation import CrossSectionalResidualDetector\n"
)
INIT_ANCHOR = "            self.leadership = MarketLeadershipGate(SYMBOLS, lookback_bars=1440)\n"
INIT_REPLACEMENT = (
    "            self.leadership = MarketLeadershipGate(SYMBOLS, lookback_bars=1440)\n"
    "            self.candidate33 = CrossSectionalResidualDetector(SYMBOLS)\n"
)
PLAN_ANCHOR = '''                plans.append((plan, candidate))

            if not plans:
                return
'''
PLAN_REPLACEMENT = '''                plans.append((plan, candidate))

            # candidate-33-cross-sectional-dislocation: local engines have now
            # observed the same completed minute. These plans carry their own
            # synchronized peer contract and therefore bypass the ordinary FAR
            # market-leadership classifier, but still enter the same global
            # arbitration, exact-NAV sizer and Nautilus order boundary.
            blocked_symbols = {candidate.symbol for _, candidate in plans}
            cross_sectional = self.candidate33.on_batch(
                ts_ns,
                self.buffer,
                self.logic,
                blocked_symbols=blocked_symbols,
            )
            for event_symbol in SYMBOLS:
                self._capture_events(event_symbol)
            for candidate_symbol, candidate_plan in cross_sectional:
                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[candidate_symbol].mark_rejected(
                        candidate_plan,
                        ts_ns,
                        "OUTSIDE_EVALUATION_WINDOW",
                    )
                    self._capture_events(candidate_symbol)
                    continue
                candidate = Candidate(
                    symbol=candidate_symbol,
                    scenario_id=candidate_plan.scenario_id,
                    observed_ts_ns=candidate_plan.observed_ts_ns,
                    net_structural_r=Decimal(str(candidate_plan.net_r)),
                    expected_entry=Decimal(str(candidate_plan.expected_entry)),
                    expected_loss_per_unit=Decimal(str(candidate_plan.loss_per_unit)),
                )
                plans.append((candidate_plan, candidate))

            if not plans:
                return
'''
MARKER = "candidate-33-cross-sectional-dislocation"


def materialize_candidate33_portfolio_source(source: str) -> str:
    if MARKER in source:
        return source
    for anchor, name in (
        (IMPORT_ANCHOR, "import"),
        (INIT_ANCHOR, "init"),
        (PLAN_ANCHOR, "plan"),
    ):
        count = source.count(anchor)
        if count != 1:
            raise RuntimeError(
                f"Candidate 33 {name} anchor drifted: expected one, found {count}",
            )
    source = source.replace(IMPORT_ANCHOR, IMPORT_REPLACEMENT, 1)
    source = source.replace(INIT_ANCHOR, INIT_REPLACEMENT, 1)
    source = source.replace(PLAN_ANCHOR, PLAN_REPLACEMENT, 1)
    if source.count(MARKER) != 1:
        raise RuntimeError("Candidate 33 portfolio branch was not materialized once")
    return source
