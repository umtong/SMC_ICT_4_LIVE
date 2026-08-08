#!/usr/bin/env python3
"""Fail-closed source materialization for Candidate 13 V10."""
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
            f"Candidate 13 V10 portfolio boundary drifted at {label}: "
            f"expected {expected} occurrence(s), found {count}",
        )
    return source.replace(old, new)


def materialize_persistent_quarter_hour_source(source: str) -> str:
    source = _replace(
        source,
        '        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()\n'
        '        if len(frame.index) not in (1439, 1440, 1441):',
        '        numeric_open_time = pd.to_numeric(frame["open_time"], errors="coerce")\n'
        '        valid_open_time = numeric_open_time.notna()\n'
        '        frame = frame.loc[valid_open_time].copy()\n'
        '        frame["open_time"] = numeric_open_time.loc[valid_open_time].astype("int64")\n'
        '        # candidate-13-v10-strict-open-time: normalize mixed historical archives.\n'
        '        if len(frame.index) not in (1439, 1440, 1441):',
        label="strict-open-time",
    )
    source = _replace(
        source,
        "from session_engine import RegionalHandoffAuctionEngine\n\n"
        "from smc_ict_4.event_log import EventLogError, write_events",
        "from session_engine import RegionalHandoffAuctionEngine\n"
        "from quarter_hour_persistent_initiative import (\n"
        "    QHI_ROUTER_KEY,\n"
        "    PersistentInitiativeContinuationEngine,\n"
        "    PersistentQuarterHourRouter,\n"
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
            self.initiative_key = QHI_ROUTER_KEY
            self.logic[self.initiative_key] = PersistentQuarterHourRouter(logic_config)
            self.continuation_keys = {
                symbol: f"{symbol}::PERSISTENT_QH_CONTINUATION"
                for symbol in SYMBOLS
            }
            for symbol, logic_key in self.continuation_keys.items():
                self.logic[logic_key] = PersistentInitiativeContinuationEngine(
                    logic_config,
                    str(instruments[symbol].id),
                    symbol=symbol,
                    logic_key=logic_key,
                )
            self.sizer = RiskSizer(logic_config.risk_fraction)''',
        label="strategy-persistent-initiative-engines",
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
        '''            initiative_state = self.logic[self.initiative_key].on_batch(
                ts_ns,
                self.buffer,
            )
            self._capture_events(self.initiative_key)
            plans: list[tuple[TradePlan, Candidate]] = []
''',
        label="initiative-state-observation",
    )
    source = _replace(
        source,
        '''                plans.append((plan, candidate))

            if not plans:
                return''',
        '''                plans.append((plan, candidate))

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
                continuation_candidate = Candidate(
                    symbol=symbol,
                    scenario_id=continuation.scenario_id,
                    observed_ts_ns=continuation.observed_ts_ns,
                    net_structural_r=Decimal(str(continuation.net_r)),
                    expected_entry=Decimal(str(continuation.expected_entry)),
                    expected_loss_per_unit=Decimal(str(continuation.loss_per_unit)),
                )
                plans.append((continuation, continuation_candidate))

            if not plans:
                return''',
        label="post-activation-continuation-generation",
    )
    source = _replace(
        source,
        '''        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
''',
        '''        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            # candidate-13-v10-protective-rejection-fail-close:
            # a child rejection after a parent fill must never leave naked risk.
            if self.active_plan is not None and self.active_symbol is not None:
                instrument_id = instruments[self.active_symbol].id
                if not self.portfolio.is_flat(instrument_id):
                    self.lifecycle.append({
                        "type": "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED",
                        "ts_event": int(event.ts_event),
                        "scenario_id": self.active_plan.scenario_id,
                        "symbol": self.active_symbol,
                        "rejected_client_order_id": str(event.client_order_id),
                    })
                    if self.cache.orders_open_count(
                        instrument_id=instrument_id,
                        strategy_id=self.id,
                    ):
                        self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
''',
        label="protective-rejection-fail-close",
    )
    required = (
        "candidate-13-v10-strict-open-time",
        "PersistentQuarterHourRouter(logic_config)",
        "PersistentInitiativeContinuationEngine(",
        "initiative_state = self.logic[self.initiative_key].on_batch(",
        "plans.append((continuation, continuation_candidate))",
        "candidate-13-v10-protective-rejection-fail-close",
    )
    missing = [token for token in required if source.count(token) != 1]
    if missing:
        raise RuntimeError(
            f"Candidate 13 V10 routes were not materialized exactly once: {missing}",
        )
    return source
