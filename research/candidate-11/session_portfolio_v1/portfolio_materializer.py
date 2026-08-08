"""Fail-closed materialization of Candidate 11's multi-session portfolio.

The inherited Candidate 13 runner remains the execution source of truth. This
transformer installs the byte-identical Candidate 12 I7 state machine on every
allowed instrument, sends each completed local session scenario through the
same frozen four-market semantic gate, and only then allows it to compete in
the existing GlobalCandidateMutex.

No alpha threshold is relaxed. The only economic change from the locked
Candidate 14 baseline is opportunity symmetry: BTC, ETH, SOL and XRP may each
originate a fully completed session auction, with their own exact tick size.
Every replacement is an exact source contract; upstream drift aborts before
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
            f"Candidate 11 multi-session boundary drifted at {label}: "
            f"expected {expected} occurrence(s), found {count}",
        )
    return source.replace(old, new)


def materialize_combined_portfolio_source(source: str) -> str:
    source = _replace(
        source,
        "from session_engine import RegionalHandoffAuctionEngine\n\n"
        "from smc_ict_4.event_log import EventLogError, write_events",
        "from session_engine import RegionalHandoffAuctionEngine\n"
        "from session_auction_bridge import SessionAuctionBridge, session_logic_key\n"
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
            self.session_logic_keys = {
                symbol: session_logic_key(symbol)
                for symbol in SYMBOLS
            }
            for symbol in SYMBOLS:
                logic_key = self.session_logic_keys[symbol]
                self.logic[logic_key] = SessionAuctionBridge(
                    session_logic_config,
                    str(instruments[symbol].id),
                    logic_key=logic_key,
                    price_increment=float(META[symbol]["price_increment"]),
                )
            self.sizer = RiskSizer(logic_config.risk_fraction)''',
        label="strategy-multi-session-engines",
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
        '''            # Every allowed market owns the same completed-session I7
            # detector. A local plan still needs unanimous four-market transfer,
            # causal efficiency/displacement and top-half price discovery before
            # it may compete for the single global account slot.
            for session_symbol in SYMBOLS:
                session_logic_key_value = self.session_logic_keys[session_symbol]
                session_engine = self.logic[session_logic_key_value]
                session_plan = session_engine.on_bar(
                    self.buffer[session_symbol],
                    allow_entry=True,
                )
                self._capture_events(session_logic_key_value)
                if session_plan is None:
                    continue
                if ts_ns < self.config.evaluation_start_ns:
                    session_engine.mark_rejected(
                        session_plan,
                        ts_ns,
                        "OUTSIDE_EVALUATION_WINDOW",
                    )
                    self._capture_events(session_logic_key_value)
                    continue

                semantic_scenario = str(
                    session_plan.details.get("market_semantic_scenario", "UNSUPPORTED")
                )
                causal_start_ts_ns = int(
                    session_plan.details.get("causal_start_ts_ns", -1)
                )
                session_leadership = self.leadership.decide_session(
                    symbol=session_symbol,
                    scenario=semantic_scenario,
                    direction=session_plan.direction.value,
                    sweep_ts_ns=causal_start_ts_ns,
                    confirmation_ts_ns=int(session_plan.observed_ts_ns),
                )
                session_plan.details["market_leadership"] = session_leadership.to_dict()
                if not session_leadership.approved:
                    reason = f"SESSION_{session_leadership.reason}"
                    session_engine.mark_rejected(
                        session_plan,
                        ts_ns,
                        reason,
                        session_leadership.to_dict(),
                    )
                    self._capture_events(session_logic_key_value)
                    self.rejections.append({
                        "type": "SESSION_MARKET_LEADERSHIP_REJECTED",
                        "observed_ts_ns": session_plan.observed_ts_ns,
                        "causal_start_ts_ns": causal_start_ts_ns,
                        "scenario_id": session_plan.scenario_id,
                        "scenario": session_plan.scenario.value,
                        "market_semantic_scenario": semantic_scenario,
                        "symbol": session_symbol,
                        "reason": reason,
                        "leader": session_leadership.leader,
                        "peer_returns": session_leadership.peer_returns,
                        "net_structural_r": str(session_plan.net_r),
                    })
                    continue

                session_candidate = Candidate(
                    symbol=session_symbol,
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
        label="multi-session-semantic-arbitration",
    )
    if source.count("Every allowed market owns the same completed-session I7") != 1:
        raise RuntimeError("Candidate 11 multi-session semantic gate was not materialized once")
    if 'self.buffer["BTCUSDT"]' in source and "session_plan =" in source:
        # A remaining hard-coded BTC session feed would invalidate the symmetry
        # hypothesis even if ordinary SCDAM code still mentions BTC elsewhere.
        raise RuntimeError("hard-coded BTC-only session feed remains")
    return source
