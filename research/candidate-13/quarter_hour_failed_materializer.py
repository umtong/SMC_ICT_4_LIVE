#!/usr/bin/env python3
"""Fail-closed source materialization for Candidate 13 V11."""
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
            f"Candidate 13 V11 portfolio boundary drifted at {label}: "
            f"expected {expected} occurrence(s), found {count}",
        )
    return source.replace(old, new)


def materialize_failed_initiative_source(source: str) -> str:
    source = _replace(
        source,
        '        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()\n'
        '        if len(frame.index) not in (1439, 1440, 1441):',
        '        numeric_open_time = pd.to_numeric(frame["open_time"], errors="coerce")\n'
        '        valid_open_time = numeric_open_time.notna()\n'
        '        frame = frame.loc[valid_open_time].copy()\n'
        '        frame["open_time"] = numeric_open_time.loc[valid_open_time].astype("int64")\n'
        '        # candidate-13-v11-strict-open-time: normalize mixed historical archives.\n'
        '        if len(frame.index) not in (1439, 1440, 1441):',
        label="strict-open-time",
    )
    source = _replace(
        source,
        "from session_engine import RegionalHandoffAuctionEngine\n\n"
        "from smc_ict_4.event_log import EventLogError, write_events",
        "from session_engine import RegionalHandoffAuctionEngine\n"
        "from quarter_hour_failed_initiative import (\n"
        "    QHF_LOGIC_KEY,\n"
        "    QuarterHourFailedInitiativeEngine,\n"
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
            self.failed_initiative_key = QHF_LOGIC_KEY
            self.logic[self.failed_initiative_key] = QuarterHourFailedInitiativeEngine(
                logic_config,
            )
            self.sizer = RiskSizer(logic_config.risk_fraction)''',
        label="strategy-failed-initiative-engine",
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
        '''                plans.append((plan, candidate))

            if not plans:
                return''',
        '''                plans.append((plan, candidate))

            for failure_symbol, failure_plan in self.logic[
                self.failed_initiative_key
            ].on_batch(ts_ns, self.buffer):
                self._capture_events(self.failed_initiative_key)
                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[self.failed_initiative_key].mark_rejected(
                        failure_plan,
                        ts_ns,
                        "OUTSIDE_EVALUATION_WINDOW",
                    )
                    self._capture_events(self.failed_initiative_key)
                    continue
                failure_candidate = Candidate(
                    symbol=failure_symbol,
                    scenario_id=failure_plan.scenario_id,
                    observed_ts_ns=failure_plan.observed_ts_ns,
                    net_structural_r=Decimal(str(failure_plan.net_r)),
                    expected_entry=Decimal(str(failure_plan.expected_entry)),
                    expected_loss_per_unit=Decimal(str(failure_plan.loss_per_unit)),
                )
                plans.append((failure_plan, failure_candidate))
            self._capture_events(self.failed_initiative_key)

            if not plans:
                return''',
        label="failed-initiative-plan-generation",
    )
    source = _replace(
        source,
        '''            self.active_plan: TradePlan | None = None
            self.active_symbol: str | None = None
            self.last_ts_ns = 0

        def on_start(self) -> None:''',
        '''            self.active_plan: TradePlan | None = None
            self.active_symbol: str | None = None
            self.last_ts_ns = 0
            self.protective_exit_pending = False

        def on_start(self) -> None:''',
        label="protective-market-exit-state",
    )
    source = _replace(
        source,
        '''        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._release_if_terminal(self.last_ts_ns, "BAR_TERMINAL_SYNC")
            if self.last_ts_ns >= self.config.evaluation_end_ns:''',
        '''        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            if self.protective_exit_pending:
                if not self.is_exiting():
                    self.lifecycle.append({
                        "type": "PROTECTIVE_MARKET_EXIT_RETRY",
                        "ts_event": self.last_ts_ns,
                        "scenario_id": None if self.active_plan is None else self.active_plan.scenario_id,
                        "symbol": self.active_symbol,
                    })
                    self.market_exit()
                return
            if self.is_exiting():
                return
            self._release_if_terminal(self.last_ts_ns, "BAR_TERMINAL_SYNC")
            if self.last_ts_ns >= self.config.evaluation_end_ns:''',
        label="protective-market-exit-bar-guard",
    )
    source = _replace(
        source,
        '''        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")

        def on_order_expired(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_EXPIRED")
            self._fail_close_partial_entry(event)
            self._release_if_terminal(int(event.ts_event), "ENTRY_EXPIRED")

        def on_order_canceled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_CANCELED")
            self._release_if_terminal(int(event.ts_event), "ORDER_CANCELED")

        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")

        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
''',
        '''        def _begin_protective_market_exit(
            self,
            event: OrderEvent,
            event_type: str,
        ) -> None:
            if self.active_plan is None or self.active_symbol is None:
                return
            instrument_id = instruments[self.active_symbol].id
            if self.portfolio.is_flat(instrument_id):
                return
            if self.protective_exit_pending or self.is_exiting():
                return
            self.protective_exit_pending = True
            self.lifecycle.append({
                "type": "PROTECTIVE_ORDER_FAILURE_MARKET_EXIT",
                "order_event_type": event_type,
                "ts_event": int(event.ts_event),
                "scenario_id": self.active_plan.scenario_id,
                "symbol": self.active_symbol,
                "failed_client_order_id": str(event.client_order_id),
            })
            # candidate-13-v11-protective-failure-fail-close:
            # delegate cancel/in-flight reconciliation/residual flattening to NautilusTrader.
            # candidate-13-v11-protective-market-exit
            self.market_exit()

        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            if self.protective_exit_pending or self.is_exiting():
                return
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")

        def on_order_expired(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_EXPIRED")
            if self.protective_exit_pending or self.is_exiting():
                return
            self._fail_close_partial_entry(event)
            self._release_if_terminal(int(event.ts_event), "ENTRY_EXPIRED")

        def on_order_canceled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_CANCELED")
            if self.protective_exit_pending or self.is_exiting():
                return
            self._release_if_terminal(int(event.ts_event), "ORDER_CANCELED")

        def on_order_denied(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            self.errors.append({"type": "ORDER_DENIED", "event": str(event)})
            self._begin_protective_market_exit(event, "ORDER_DENIED")
            if self.protective_exit_pending or self.is_exiting():
                return
            self._release_if_terminal(int(event.ts_event), "ORDER_DENIED")

        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._begin_protective_market_exit(event, "ORDER_REJECTED")
            if self.protective_exit_pending or self.is_exiting():
                return
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")

        def post_market_exit(self) -> None:
            if not self.protective_exit_pending:
                return
            flat = self._all_flat()
            open_orders = self._open_orders()
            self.lifecycle.append({
                "type": "PROTECTIVE_MARKET_EXIT_COMPLETE" if flat and open_orders == 0 else "PROTECTIVE_MARKET_EXIT_INCOMPLETE",
                "ts_event": self.last_ts_ns,
                "scenario_id": None if self.active_plan is None else self.active_plan.scenario_id,
                "symbol": self.active_symbol,
                "flat": flat,
                "open_orders": open_orders,
            })
            if not flat or open_orders:
                self.errors.append({
                    "type": "PROTECTIVE_MARKET_EXIT_INCOMPLETE",
                    "ts_ns": self.last_ts_ns,
                    "flat": flat,
                    "open_orders": open_orders,
                })
                return
            self.protective_exit_pending = False
            self._release_if_terminal(
                self.last_ts_ns,
                "PROTECTIVE_MARKET_EXIT_COMPLETE",
            )
''',
        label="protective-order-failure-market-exit",
    )
    required = (
        "candidate-13-v11-strict-open-time",
        "QuarterHourFailedInitiativeEngine(\n                logic_config,",
        "].on_batch(ts_ns, self.buffer):",
        "plans.append((failure_plan, failure_candidate))",
        "candidate-13-v11-protective-failure-fail-close",
        "candidate-13-v11-protective-market-exit",
        "def post_market_exit(self) -> None:",
        "self.protective_exit_pending = True",
    )
    missing = [token for token in required if source.count(token) != 1]
    if missing:
        raise RuntimeError(
            f"Candidate 13 V11 routes were not materialized exactly once: {missing}",
        )
    return source
