"""Fail-closed source materialization for the Candidate 14 combined portfolio.

The inherited Candidate 13 runner is intentionally retained as the execution
source of truth.  This transformer inserts Candidate 12 I7 as a plan-producing
BTC session module *inside the same strategy*, then sends both modules through
the existing GlobalCandidateMutex, exact NAV risk sizer and Nautilus account.
Every replacement is an exact source contract; upstream drift aborts before any
market data is downloaded.
"""
from __future__ import annotations


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
            f"Candidate 14 portfolio boundary drifted at {label}: "
            f"expected {expected} occurrence(s), found {count}",
        )
    return source.replace(old, new)


def materialize_combined_portfolio_source(source: str) -> str:
    source = _replace(
        source,
        "from session_engine import RegionalHandoffAuctionEngine\n\n"
        "from smc_ict_4.event_log import EventLogError, write_events",
        "from session_engine import RegionalHandoffAuctionEngine\n"
        "from session_auction_bridge import SessionAuctionBridge, SESSION_LOGIC_KEY\n"
        "from session_auction_i7 import LogicConfig as SessionLogicConfig\n\n"
        "from smc_ict_4.event_log import EventLogError, write_events",
        label="imports",
    )
    source = _replace(
        source,
        "    logics: dict[str, RegionalHandoffAuctionEngine],",
        "    logics: dict[str, Any],",
        label="metrics-logic-type",
    )
    source = _replace(
        source,
        '''        "scenario_counts": {
            scenario: sum(plan["scenario"] == scenario for plan in plans)
            for scenario in ("FAR", "AAC")
        },
        "symbol_counts": {''',
        '''        "scenario_counts": {
            scenario: sum(plan["scenario"] == scenario for plan in plans)
            for scenario in sorted({str(plan.get("scenario", "UNKNOWN")) for plan in plans})
        },
        "module_counts": {
            module: sum(plan.get("module", "SCDAM_CORE") == module for plan in plans)
            for module in sorted({str(plan.get("module", "SCDAM_CORE")) for plan in plans})
        },
        "symbol_counts": {''',
        label="dynamic-scenario-metrics",
    )
    source = _replace(
        source,
        '    logic_config = LogicConfig(**config["logic"])\n',
        '    logic_config = LogicConfig(**config["logic"])\n'
        '    session_logic_config = SessionLogicConfig(**config["session_i7"]["logic"])\n',
        label="session-config",
    )
    source = _replace(
        source,
        '''            self.logic = {
                symbol: RegionalHandoffAuctionEngine(logic_config, str(instruments[symbol].id))
                for symbol in SYMBOLS
            }
            self.sizer = RiskSizer(logic_config.risk_fraction)''',
        '''            self.logic = {
                symbol: RegionalHandoffAuctionEngine(logic_config, str(instruments[symbol].id))
                for symbol in SYMBOLS
            }
            self.session_logic_key = SESSION_LOGIC_KEY
            self.logic[self.session_logic_key] = SessionAuctionBridge(
                session_logic_config,
                str(instruments["BTCUSDT"].id),
                logic_key=self.session_logic_key,
            )
            self.sizer = RiskSizer(logic_config.risk_fraction)''',
        label="strategy-session-engine",
    )
    source = _replace(
        source,
        "            self.event_cursor = {symbol: 0 for symbol in SYMBOLS}",
        "            self.event_cursor = {logic_key: 0 for logic_key in self.logic}",
        label="event-cursors",
    )
    source = _replace(
        source,
        '''        def _capture_events(self, symbol: str) -> None:
            engine = self.logic[symbol]
            cursor = self.event_cursor[symbol]
            if cursor < len(engine.events):
                self.events.extend(engine.events[cursor:])
                self.event_cursor[symbol] = len(engine.events)
''',
        '''        def _capture_events(self, logic_key: str) -> None:
            engine = self.logic[logic_key]
            cursor = self.event_cursor[logic_key]
            if cursor < len(engine.events):
                self.events.extend(engine.events[cursor:])
                self.event_cursor[logic_key] = len(engine.events)
''',
        label="event-capture-routing",
    )
    source = _replace(
        source,
        '''        def _all_flat(self) -> bool:
            return all(self.portfolio.is_flat(instrument_id) for instrument_id in self.config.instrument_ids)

        def _account_values(self) -> tuple[Decimal, Decimal]:''',
        '''        def _all_flat(self) -> bool:
            return all(self.portfolio.is_flat(instrument_id) for instrument_id in self.config.instrument_ids)

        @staticmethod
        def _logic_key_for_plan(plan: Any | None, symbol: str) -> str:
            if plan is None:
                return symbol
            details = getattr(plan, "details", {})
            return str(details.get("_logic_key", symbol))

        def _logic_for_plan(self, plan: Any, symbol: str) -> Any:
            return self.logic[self._logic_key_for_plan(plan, symbol)]

        def _account_values(self) -> tuple[Decimal, Decimal]:''',
        label="plan-origin-routing",
    )
    source = _replace(
        source,
        "self.logic[self.active_symbol]",
        "self._logic_for_plan(self.active_plan, self.active_symbol)",
        label="active-lifecycle-routing",
        expected=3,
    )
    source = _replace(
        source,
        "self._capture_events(self.active_symbol)",
        "self._capture_events(self._logic_key_for_plan(self.active_plan, self.active_symbol))",
        label="active-event-routing",
        expected=3,
    )
    source = _replace(
        source,
        "self.logic[symbol].mark_rejected(",
        "self._logic_for_plan(plan, symbol).mark_rejected(",
        label="symbol-rejection-routing",
        expected=5,
    )
    source = _replace(
        source,
        "self.logic[symbol].mark_submitted(",
        "self._logic_for_plan(plan, symbol).mark_submitted(",
        label="submission-routing",
    )
    source = _replace(
        source,
        "self._capture_events(symbol)",
        "self._capture_events(self._logic_key_for_plan(plan, symbol))",
        label="symbol-event-routing",
        expected=7,
    )
    source = _replace(
        source,
        "self.logic[rejected.symbol].mark_rejected(",
        "self._logic_for_plan(plan, rejected.symbol).mark_rejected(",
        label="arbitration-rejection-routing",
        expected=2,
    )
    source = _replace(
        source,
        "self._capture_events(rejected.symbol)",
        "self._capture_events(self._logic_key_for_plan(plan, rejected.symbol))",
        label="arbitration-event-routing",
        expected=2,
    )
    source = _replace(
        source,
        '''            self.plans.append({
                "symbol": symbol,
                "scenario_id": plan.scenario_id,''',
        '''            self.plans.append({
                "symbol": symbol,
                "module": str(plan.details.get("module", "SCDAM_CORE")),
                "scenario_id": plan.scenario_id,''',
        label="plan-module-evidence",
    )
    source = _replace(
        source,
        '''            if not plans:
                return
            plan_by_id = {plan.scenario_id: (plan, candidate) for plan, candidate in plans}''',
        '''            # Candidate 12 I7 observes only BTC, but competes for the
            # exact same global slot as every four-market SCDAM plan.
            session_plan = self.logic[self.session_logic_key].on_bar(
                self.buffer["BTCUSDT"],
                allow_entry=True,
            )
            self._capture_events(self.session_logic_key)
            if session_plan is not None:
                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[self.session_logic_key].mark_rejected(
                        session_plan,
                        ts_ns,
                        "OUTSIDE_EVALUATION_WINDOW",
                    )
                    self._capture_events(self.session_logic_key)
                else:
                    session_candidate = Candidate(
                        symbol="BTCUSDT",
                        scenario_id=session_plan.scenario_id,
                        observed_ts_ns=session_plan.observed_ts_ns,
                        net_structural_r=Decimal(str(session_plan.net_r)),
                        expected_entry=Decimal(str(session_plan.expected_entry)),
                        expected_loss_per_unit=Decimal(str(session_plan.loss_per_unit)),
                    )
                    plans.append((session_plan, session_candidate))

            if not plans:
                return
            plan_by_id = {plan.scenario_id: (plan, candidate) for plan, candidate in plans}''',
        label="session-plan-arbitration",
    )
    if source.count("Candidate 12 I7 observes only BTC") != 1:
        raise RuntimeError("Candidate 14 session module was not materialized exactly once")
    return source
