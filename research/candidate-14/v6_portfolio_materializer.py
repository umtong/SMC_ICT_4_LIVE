"""Fail-closed source materialization for Candidate 14 V6.

The inherited four-market Nautilus portfolio remains the execution source of
truth. This transformer adds only:

* one marketwide initiative state owned by V6 event leadership; and
* one detector-only five-minute MSS/FVG continuation engine per symbol.

All emitted plans compete through the existing GlobalCandidateMutex, exact
current-NAV three-percent loss sizer, and Nautilus bracket-order path. Every
replacement is an exact source contract; upstream drift aborts before data is
downloaded.
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
            f"Candidate 14 V6 portfolio boundary drifted at {label}: "
            f"expected {expected} occurrence(s), found {count}",
        )
    return source.replace(old, new)


def materialize_v6_portfolio_source(source: str) -> str:
    source = _replace(
        source,
        "from session_engine import RegionalHandoffAuctionEngine\n\n"
        "from smc_ict_4.event_log import EventLogError, write_events",
        "from session_engine import RegionalHandoffAuctionEngine\n"
        "from global_initiative_continuation import (\n"
        "    CONTINUATION_MODULE,\n"
        "    GLOBAL_INITIATIVE_KEY,\n"
        "    GlobalInitiativeRouter,\n"
        "    InitiativeContinuationEngine,\n"
        ")\n\n"
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
        label="dynamic-module-metrics",
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
            self.initiative_key = GLOBAL_INITIATIVE_KEY
            self.logic[self.initiative_key] = GlobalInitiativeRouter()
            self.continuation_keys = {
                symbol: f"{symbol}::GLOBAL_INITIATIVE_CONTINUATION"
                for symbol in SYMBOLS
            }
            for symbol, logic_key in self.continuation_keys.items():
                self.logic[logic_key] = InitiativeContinuationEngine(
                    logic_config,
                    str(instruments[symbol].id),
                    symbol=symbol,
                    logic_key=logic_key,
                )
            self.sizer = RiskSizer(logic_config.risk_fraction)''',
        label="strategy-initiative-engines",
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
        "            plans: list[tuple[TradePlan, Candidate]] = []\n",
        '''            self.logic[self.initiative_key].observe_batch(ts_ns, self.buffer)
            self._capture_events(self.initiative_key)
            plans: list[tuple[TradePlan, Candidate]] = []
''',
        label="initiative-batch-lifecycle",
    )
    source = _replace(
        source,
        '''                candidate = Candidate(
                    symbol=symbol,
                    scenario_id=plan.scenario_id,
                    observed_ts_ns=plan.observed_ts_ns,
                    net_structural_r=Decimal(str(plan.net_r)),
                    expected_entry=Decimal(str(plan.expected_entry)),
                    expected_loss_per_unit=Decimal(str(plan.loss_per_unit)),
                )
                plans.append((plan, candidate))

            if not plans:
                return''',
        '''                self.logic[self.initiative_key].observe_owned_plan(
                    plan=plan,
                    symbol=symbol,
                    leadership=leadership.to_dict(),
                    observed_ts_ns=ts_ns,
                )
                self._capture_events(self.initiative_key)
                candidate = Candidate(
                    symbol=symbol,
                    scenario_id=plan.scenario_id,
                    observed_ts_ns=plan.observed_ts_ns,
                    net_structural_r=Decimal(str(plan.net_r)),
                    expected_entry=Decimal(str(plan.expected_entry)),
                    expected_loss_per_unit=Decimal(str(plan.loss_per_unit)),
                )
                plans.append((plan, candidate))

            initiative_state = self.logic[self.initiative_key].state
            for symbol in SYMBOLS:
                logic_key = self.continuation_keys[symbol]
                continuation = self.logic[logic_key].on_bar(
                    self.buffer[symbol],
                    state=initiative_state,
                    external_engine=self.logic[symbol],
                )
                self._capture_events(logic_key)
                if continuation is None:
                    continue
                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[logic_key].mark_rejected(
                        continuation,
                        ts_ns,
                        "OUTSIDE_EVALUATION_WINDOW",
                    )
                    self._capture_events(logic_key)
                    continue
                candidate = Candidate(
                    symbol=symbol,
                    scenario_id=continuation.scenario_id,
                    observed_ts_ns=continuation.observed_ts_ns,
                    net_structural_r=Decimal(str(continuation.net_r)),
                    expected_entry=Decimal(str(continuation.expected_entry)),
                    expected_loss_per_unit=Decimal(str(continuation.loss_per_unit)),
                )
                plans.append((continuation, candidate))

            if not plans:
                return''',
        label="owned-state-continuation-generation",
    )
    if source.count("GLOBAL_INITIATIVE_CONTINUATION") < 2:
        raise RuntimeError("Candidate 14 V6 continuation route was not materialized")
    if source.count("observe_owned_plan") != 1:
        raise RuntimeError("Candidate 14 V6 initiative ownership was not inserted exactly once")
    return source
