"""Candidate 15 V5 portfolio materialization.

V5 isolates a timeframe-consistent, response-qualified cross-market initiative
family. Prior SCDAM and SESSION_I7 plans remain fail-closed. NautilusTrader keeps
exclusive ownership of fills, costs, account state and the one-global-slot rule.
"""
from __future__ import annotations

from math import isfinite


def far_stop_preserves_sweep_invalidation(
    direction: str,
    stop: float,
    sweep_invalidation: float | None,
) -> bool:
    """Preserved V3 invariant for any future FAR-family reactivation."""
    if sweep_invalidation is None:
        return False
    stop_value = float(stop)
    reference = float(sweep_invalidation)
    if not isfinite(stop_value) or not isfinite(reference):
        return False
    epsilon = max(abs(stop_value), abs(reference), 1.0) * 1e-12
    if direction == "LONG":
        return stop_value <= reference + epsilon
    if direction == "SHORT":
        return stop_value >= reference - epsilon
    return False


def _replace(source: str, old: str, new: str, *, label: str, expected: int = 1) -> str:
    count = source.count(old)
    if count != expected:
        raise RuntimeError(
            f"Candidate 15 V5 portfolio boundary drifted at {label}: "
            f"expected {expected}, found {count}",
        )
    return source.replace(old, new)


def materialize_candidate15_portfolio_source(source: str) -> str:
    source = _replace(
        source,
        '        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()\n'
        '        if len(frame.index) not in (1439, 1440, 1441):',
        '        numeric_open_time = pd.to_numeric(frame["open_time"], errors="coerce")\n'
        '        valid_open_time = numeric_open_time.notna()\n'
        '        frame = frame.loc[valid_open_time].copy()\n'
        '        frame["open_time"] = numeric_open_time.loc[valid_open_time].astype("int64")\n'
        '        # candidate-15-v5-strict-open-time\n'
        '        if len(frame.index) not in (1439, 1440, 1441):',
        label="strict-open-time",
    )

    initialization = '''            self.session_logic_key = SESSION_LOGIC_KEY
            self.logic[self.session_logic_key] = SessionAuctionBridge(
                session_logic_config,
                str(instruments["BTCUSDT"].id),
                logic_key=self.session_logic_key,
            )
            self.sizer = RiskSizer(logic_config.risk_fraction)'''
    initialization_v5 = '''            self.session_logic_key = SESSION_LOGIC_KEY
            self.logic[self.session_logic_key] = SessionAuctionBridge(
                session_logic_config,
                str(instruments["BTCUSDT"].id),
                logic_key=self.session_logic_key,
            )
            self.initiative_key = QHI_ROUTER_KEY
            self.logic[self.initiative_key] = ResponseQualifiedPersistentQuarterHourRouter(
                logic_config,
            )
            self.continuation_keys = {
                symbol: f"{symbol}::C15_RESPONSE_QUALIFIED_QH_CONTINUATION"
                for symbol in SYMBOLS
            }
            for symbol, logic_key in self.continuation_keys.items():
                self.logic[logic_key] = PersistentInitiativeContinuationEngine(
                    logic_config,
                    str(instruments[symbol].id),
                    symbol=symbol,
                    logic_key=logic_key,
                )
            self.sizer = RiskSizer(logic_config.risk_fraction)'''
    source = _replace(
        source,
        initialization,
        initialization_v5,
        label="response-qualified-initiative-engines",
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
        "                leadership = self.leadership.decide(\n",
        '''                self._logic_for_plan(plan, symbol).mark_rejected(
                    plan,
                    ts_ns,
                    "C15_V5_CORE_FAMILY_QUARANTINED",
                    {
                        "candidate15_state": "NO_TRADE",
                        "candidate15_policy": "FAILED_FAMILY_QUARANTINE",
                        "prior_evidence": "V3 predeclared screen failed activity and growth",
                    },
                )
                self._capture_events(self._logic_key_for_plan(plan, symbol))
                self.rejections.append({
                    "type": "C15_V5_CORE_FAMILY_QUARANTINED",
                    "observed_ts_ns": plan.observed_ts_ns,
                    "scenario_id": plan.scenario_id,
                    "scenario": plan.scenario.value,
                    "symbol": symbol,
                    "reason": "C15_V5_CORE_FAMILY_QUARANTINED",
                    "net_structural_r": str(plan.net_r),
                })
                continue
                leadership = self.leadership.decide(
''',
        label="core-family-quarantine",
    )

    source = _replace(
        source,
        '''            # Candidate 12 I7 observes BTC only.  Each complete local
            # session plan must pass the dedicated four-market session semantic''',
        '''            for symbol in SYMBOLS:
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

            # Candidate 12 I7 observes BTC only.  Each complete local
            # session plan must pass the dedicated four-market session semantic''',
        label="response-qualified-continuation-generation",
    )

    source = _replace(
        source,
        "                        plans.append((session_plan, session_candidate))",
        '''                        self.logic[self.session_logic_key].mark_rejected(
                            session_plan,
                            ts_ns,
                            "C15_UNROUTED_SCENARIO_FAMILY",
                            {
                                "candidate15_state": "UNRESOLVED",
                                "candidate15_policy": "FAIL_CLOSED",
                                "reason": (
                                    "SESSION_I7 lacks Candidate 15 V5's timeframe-"
                                    "consistent repeated cross-market response state"
                                ),
                            },
                        )
                        self._capture_events(self.session_logic_key)
                        self.rejections.append({
                            "type": "C15_UNROUTED_SCENARIO_FAMILY",
                            "observed_ts_ns": session_plan.observed_ts_ns,
                            "causal_start_ts_ns": causal_start_ts_ns,
                            "scenario_id": session_plan.scenario_id,
                            "scenario": session_plan.scenario.value,
                            "market_semantic_scenario": semantic_scenario,
                            "symbol": "BTCUSDT",
                            "reason": "C15_UNROUTED_SCENARIO_FAMILY",
                            "net_structural_r": str(session_plan.net_r),
                        })''',
        label="session-family-fail-closed",
    )

    # A post-only parent rejection while the account is still flat is expected
    # passive non-execution, not an engine failure.  Rejection after any fill is a
    # protection failure: record an error and flatten immediately.
    source = _replace(
        source,
        '''        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            self.errors.append({"type": "ORDER_REJECTED", "event": str(event)})
            self._release_if_terminal(int(event.ts_event), "ORDER_REJECTED")
''',
        '''        def on_order_rejected(self, event: OrderEvent) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            ts_ns = int(event.ts_event)
            if self.active_plan is not None and self.active_symbol is not None:
                instrument_id = instruments[self.active_symbol].id
                account_flat = self.portfolio.is_flat(instrument_id)
                if self.mutex.state == SlotState.ENTRY_PENDING and account_flat:
                    self.lifecycle.append({
                        "type": "PASSIVE_ENTRY_REJECTED_UNFILLED",
                        "ts_event": ts_ns,
                        "scenario_id": self.active_plan.scenario_id,
                        "symbol": self.active_symbol,
                        "rejected_client_order_id": str(event.client_order_id),
                    })
                    self._release_if_terminal(ts_ns, "PASSIVE_ENTRY_REJECTED_UNFILLED")
                    return
                self.errors.append({
                    "type": "PROTECTIVE_OR_FILLED_ORDER_REJECTED",
                    "event": str(event),
                })
                if not account_flat:
                    self.lifecycle.append({
                        "type": "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED",
                        "ts_event": ts_ns,
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
            else:
                self.errors.append({"type": "UNATTRIBUTED_ORDER_REJECTED", "event": str(event)})
            self._release_if_terminal(ts_ns, "ORDER_REJECTED")
''',
        label="causal-order-rejection-classification",
    )

    required = {
        "candidate-15-v5-strict-open-time": 1,
        "ResponseQualifiedPersistentQuarterHourRouter(": 1,
        "PersistentInitiativeContinuationEngine(": 1,
        "plans.append((continuation, continuation_candidate))": 1,
        "C15_V5_CORE_FAMILY_QUARANTINED": 3,
        "C15_UNROUTED_SCENARIO_FAMILY": 3,
        "PASSIVE_ENTRY_REJECTED_UNFILLED": 3,
        "PROTECTIVE_ORDER_REJECTED_FAIL_CLOSED": 1,
    }
    bad = {
        token: (source.count(token), expected)
        for token, expected in required.items()
        if source.count(token) != expected
    }
    if bad:
        raise RuntimeError(f"Candidate 15 V5 routes were not materialized: {bad}")
    return source
