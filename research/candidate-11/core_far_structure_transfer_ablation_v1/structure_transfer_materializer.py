'''Materialize an exact post-entry structural risk-transfer ablation.

The inherited SCDAM core FAR detector, entry, initial stop, target, costs,
current-NAV 3% sizing and global one-slot contract remain unchanged. Only the
ownership of open-position risk can transfer once, after the market has formed
and causally confirmed a favorable five-minute internal pivot on the reclaimed
side of the swept pool.

This is an opened-data mechanism ablation. It cannot advance a candidate,
establish alpha or restore validation eligibility.
'''
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
            f"structural risk-transfer materialization drifted at {label}: "
            f"expected {expected} occurrence(s), found {count}",
        )
    return source.replace(old, new)


def materialize_structure_transfer_source(source: str) -> str:
    source = _replace(
        source,
        '''        max_hold_ns: int
        starting_nav: Decimal''',
        '''        max_hold_ns: int
        baseline_scenario_ids: tuple[str, ...]
        starting_nav: Decimal''',
        label="baseline-config-contract",
    )
    source = _replace(
        source,
        '''            self.active_position_opened_ns: int | None = None
            self.time_exit_requested = False
            self.resolution_exit_requested = False
            self.last_ts_ns = 0''',
        '''            self.active_position_opened_ns: int | None = None
            self.time_exit_requested = False
            self.resolution_exit_requested = False
            self.structure_transfer_requested = False
            self.structure_transfer_completed = False
            self.structure_transfer_failed = False
            self.structure_transfer_stop_order_id: str | None = None
            self.structure_transfer_requested_stop: float | None = None
            self.structure_transfer_pivot_known_ns: int | None = None
            self.active_current_stop: float | None = None
            self.last_ts_ns = 0''',
        label="risk-transfer-state",
    )
    source = _replace(
        source,
        '''                    self.active_position_opened_ns = ts_ns
                    self.time_exit_requested = False
                    self.logic[self.active_symbol].mark_entry_filled(''',
        '''                    self.active_position_opened_ns = ts_ns
                    self.time_exit_requested = False
                    self.structure_transfer_requested = False
                    self.structure_transfer_completed = False
                    self.structure_transfer_failed = False
                    self.structure_transfer_stop_order_id = None
                    self.structure_transfer_requested_stop = None
                    self.structure_transfer_pivot_known_ns = None
                    self.active_current_stop = self.active_plan.stop_price
                    self.logic[self.active_symbol].mark_entry_filled(''',
        label="entry-fill-transfer-state",
    )
    source = _replace(
        source,
        '''                    self.active_position_opened_ns = None
                    self.time_exit_requested = False''',
        '''                    self.active_position_opened_ns = None
                    self.time_exit_requested = False
                    self.structure_transfer_requested = False
                    self.structure_transfer_completed = False
                    self.structure_transfer_failed = False
                    self.structure_transfer_stop_order_id = None
                    self.structure_transfer_requested_stop = None
                    self.structure_transfer_pivot_known_ns = None
                    self.active_current_stop = None''',
        label="terminal-transfer-reset",
        expected=2,
    )
    source = _replace(
        source,
        '''        def _enforce_max_hold(self, ts_ns: int) -> None:''',
        '''        def _protective_stop_order(self):
            if self.active_symbol is None:
                return None
            instrument_id = instruments[self.active_symbol].id
            orders = self.cache.orders_open(
                instrument_id=instrument_id,
                strategy_id=self.id,
            )
            stops = [
                order
                for order in orders
                if order.order_type == OrderType.STOP_MARKET
            ]
            if len(stops) != 1:
                self.errors.append({
                    "type": "STRUCTURAL_RISK_TRANSFER_STOP_LOOKUP_FAILURE",
                    "ts_ns": self.last_ts_ns,
                    "active_symbol": self.active_symbol,
                    "active_scenario_id": (
                        None
                        if self.active_plan is None
                        else self.active_plan.scenario_id
                    ),
                    "open_order_types": [
                        str(order.order_type) for order in orders
                    ],
                    "stop_count": len(stops),
                })
                self.structure_transfer_failed = True
                return None
            return stops[0]

        def _protect_favorable_structure(self, ts_ns: int) -> None:
            if (
                self.mutex.state != SlotState.POSITION_OPEN
                or self.active_plan is None
                or self.active_symbol is None
                or self.active_position_opened_ns is None
                or self.structure_transfer_requested
                or self.structure_transfer_completed
                or self.structure_transfer_failed
            ):
                return
            if self.active_symbol not in self.buffer:
                return

            engine = self.logic[self.active_symbol]
            direction = self.active_plan.direction
            entry = float(self.active_plan.expected_entry)
            pool_level = float(self.active_plan.details["pool_level"])
            points = (
                engine.internal_lows
                if direction == Direction.LONG
                else engine.internal_highs
            )
            candidate = next(
                (
                    (event_ts_ns, known_ts_ns, float(level))
                    for event_ts_ns, known_ts_ns, level in points
                    if event_ts_ns > self.active_position_opened_ns
                    and known_ts_ns > self.active_position_opened_ns
                    and known_ts_ns <= ts_ns
                    and (
                        level > max(entry, pool_level)
                        if direction == Direction.LONG
                        else level < min(entry, pool_level)
                    )
                ),
                None,
            )
            if candidate is None:
                return

            event_ts_ns, known_ts_ns, pivot = candidate
            atr = float(engine.atr or self.active_plan.atr)
            buffer_price = float(engine.config.stop_buffer_atr) * atr
            proposed = (
                pivot - buffer_price
                if direction == Direction.LONG
                else pivot + buffer_price
            )
            instrument = instruments[self.active_symbol]
            rounded = float(str(instrument.make_price(proposed)))
            current_close = float(self.buffer[self.active_symbol].close)
            current_stop = float(
                self.active_current_stop
                if self.active_current_stop is not None
                else self.active_plan.stop_price
            )
            improves_risk = (
                rounded > current_stop
                if direction == Direction.LONG
                else rounded < current_stop
            )
            executable = (
                rounded < current_close
                if direction == Direction.LONG
                else rounded > current_close
            )
            if not improves_risk:
                self.lifecycle.append({
                    "type": "STRUCTURAL_RISK_TRANSFER_NOT_IMPROVING",
                    "ts_event": ts_ns,
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": self.active_symbol,
                    "pivot_event_ts_ns": event_ts_ns,
                    "pivot_known_ts_ns": known_ts_ns,
                    "pivot": pivot,
                    "current_stop": current_stop,
                    "proposed_stop": rounded,
                })
                self.structure_transfer_failed = True
                return
            if not executable:
                self.lifecycle.append({
                    "type": "STRUCTURAL_RISK_TRANSFER_NOT_EXECUTABLE",
                    "ts_event": ts_ns,
                    "scenario_id": self.active_plan.scenario_id,
                    "symbol": self.active_symbol,
                    "pivot_event_ts_ns": event_ts_ns,
                    "pivot_known_ts_ns": known_ts_ns,
                    "pivot": pivot,
                    "current_close": current_close,
                    "proposed_stop": rounded,
                })
                self.structure_transfer_failed = True
                return

            stop_order = self._protective_stop_order()
            if stop_order is None:
                return
            self.structure_transfer_requested = True
            self.structure_transfer_stop_order_id = str(
                stop_order.client_order_id
            )
            self.structure_transfer_requested_stop = rounded
            self.structure_transfer_pivot_known_ns = known_ts_ns
            self.lifecycle.append({
                "type": "STRUCTURAL_RISK_TRANSFER_REQUESTED",
                "ts_event": ts_ns,
                "scenario_id": self.active_plan.scenario_id,
                "symbol": self.active_symbol,
                "client_order_id": self.structure_transfer_stop_order_id,
                "direction": direction.value,
                "entry": entry,
                "pool_level": pool_level,
                "pivot_event_ts_ns": event_ts_ns,
                "pivot_known_ts_ns": known_ts_ns,
                "pivot": pivot,
                "atr": atr,
                "buffer_price": buffer_price,
                "previous_stop": current_stop,
                "requested_stop": rounded,
                "current_close": current_close,
            })
            self.modify_order(
                stop_order,
                trigger_price=instrument.make_price(rounded),
            )

        def _enforce_max_hold(self, ts_ns: int) -> None:''',
        label="risk-transfer-methods",
    )
    source = _replace(
        source,
        '''                    })
                    continue
                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[symbol].mark_rejected(plan, ts_ns, "OUTSIDE_EVALUATION_WINDOW")''',
        '''                    })
                    continue
                if plan.scenario_id not in self.config.baseline_scenario_ids:
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        "STRUCTURAL_TRANSFER_ABLATION_BASELINE_SCENARIO_ONLY",
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "SCENARIO_DOMAIN_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "scenario": plan.scenario.value,
                        "reason": (
                            "STRUCTURAL_TRANSFER_ABLATION_BASELINE_SCENARIO_ONLY"
                        ),
                    })
                    continue
                if ts_ns < self.config.evaluation_start_ns:
                    self.logic[symbol].mark_rejected(plan, ts_ns, "OUTSIDE_EVALUATION_WINDOW")''',
        label="baseline-scenario-control",
    )
    source = _replace(
        source,
        '''            if not plans:
                return
            plan_by_id = {plan.scenario_id: (plan, candidate) for plan, candidate in plans}''',
        '''            self._protect_favorable_structure(ts_ns)
            if not plans:
                return
            plan_by_id = {plan.scenario_id: (plan, candidate) for plan, candidate in plans}''',
        label="post-observation-structure-protection",
    )
    source = _replace(
        source,
        '''        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")''',
        '''        def on_order_updated(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_UPDATED")
            if (
                self.structure_transfer_requested
                and not self.structure_transfer_completed
                and self.structure_transfer_stop_order_id
                == str(event.client_order_id)
            ):
                self.structure_transfer_completed = True
                self.active_current_stop = (
                    self.structure_transfer_requested_stop
                )
                self.lifecycle.append({
                    "type": "STRUCTURAL_RISK_TRANSFER_CONFIRMED",
                    "ts_event": int(event.ts_event),
                    "scenario_id": (
                        None
                        if self.active_plan is None
                        else self.active_plan.scenario_id
                    ),
                    "symbol": self.active_symbol,
                    "client_order_id": str(event.client_order_id),
                    "confirmed_stop": self.structure_transfer_requested_stop,
                    "pivot_known_ts_ns": (
                        self.structure_transfer_pivot_known_ns
                    ),
                })

        def on_order_modify_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_MODIFY_REJECTED")
            self.errors.append({
                "type": "STRUCTURAL_RISK_TRANSFER_MODIFY_REJECTED",
                "event": str(event),
                "scenario_id": (
                    None
                    if self.active_plan is None
                    else self.active_plan.scenario_id
                ),
                "symbol": self.active_symbol,
            })
            self.structure_transfer_failed = True

        def on_order_filled(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_FILLED")
            self._release_if_terminal(int(event.ts_event), "ORDER_FILLED")''',
        label="risk-transfer-order-events",
    )
    source = _replace(
        source,
        '''        max_hold_ns=max_hold_ns,
        starting_nav=starting_nav,''',
        '''        max_hold_ns=max_hold_ns,
        baseline_scenario_ids=tuple(
            development_contract["baseline_scenario_ids"]
        ),
        starting_nav=starting_nav,''',
        label="baseline-scenario-config-instantiation",
    )
    source = _replace(
        source,
        '''        "scenario_max_hold_exit_count": sum(
            item.get("type") == "SCENARIO_MAX_HOLD_EXIT"
            for item in lifecycle
        ),''',
        '''        "structural_risk_transfer_request_count": sum(
            item.get("type") == "STRUCTURAL_RISK_TRANSFER_REQUESTED"
            for item in lifecycle
        ),
        "structural_risk_transfer_confirmed_count": sum(
            item.get("type") == "STRUCTURAL_RISK_TRANSFER_CONFIRMED"
            for item in lifecycle
        ),
        "structural_risk_transfer_not_improving_count": sum(
            item.get("type") == "STRUCTURAL_RISK_TRANSFER_NOT_IMPROVING"
            for item in lifecycle
        ),
        "structural_risk_transfer_not_executable_count": sum(
            item.get("type") == "STRUCTURAL_RISK_TRANSFER_NOT_EXECUTABLE"
            for item in lifecycle
        ),
        "scenario_max_hold_exit_count": sum(
            item.get("type") == "SCENARIO_MAX_HOLD_EXIT"
            for item in lifecycle
        ),''',
        label="risk-transfer-metrics",
    )
    source = _replace(
        source,
        '''        "candidate": "candidate-11-core-far-transfer",''',
        '''        "candidate": "candidate-11-core-far-structure-transfer-ablation",''',
        label="ablation-candidate-name",
    )

    markers = (
        "STRUCTURAL_RISK_TRANSFER_REQUESTED",
        "STRUCTURAL_RISK_TRANSFER_CONFIRMED",
        "STRUCTURAL_TRANSFER_ABLATION_BASELINE_SCENARIO_ONLY",
        "baseline_scenario_ids",
    )
    for marker in markers:
        if marker not in source:
            raise RuntimeError(
                f"structural risk-transfer marker was not installed: {marker}"
            )
    return source
