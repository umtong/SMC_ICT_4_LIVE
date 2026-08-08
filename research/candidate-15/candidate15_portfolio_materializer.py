"""Candidate 15 V4 portfolio materialization.

V1--V3 established three fail-closed invariants but the local SCDAM family was
still too sparse and its only unseen V3 trade failed at the cross-market role
router.  V4 therefore quarantines that rejected family and evaluates one
independent scenario family: persistent cross-market initiative followed by a
fresh post-activation five-minute MSS/displacement/FVG leg.

Candidate 14's Nautilus order, account, cost, current-NAV risk and one-global-slot
path remain the execution source of truth.
"""
from __future__ import annotations

from math import isfinite


def far_stop_preserves_sweep_invalidation(
    direction: str,
    stop: float,
    sweep_invalidation: float | None,
) -> bool:
    """Preserved V3 invariant for future FAR-family reactivation tests."""
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
            f"Candidate 15 V4 portfolio boundary drifted at {label}: "
            f"expected {expected}, found {count}",
        )
    return source.replace(old, new)


def materialize_candidate15_portfolio_source(source: str) -> str:
    # Historical Binance archives are not perfectly uniform about whether the
    # first row is a header.  Normalize only after causal row validation; this is
    # data integrity, not an alpha change.
    source = _replace(
        source,
        '        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()\n'
        '        if len(frame.index) not in (1439, 1440, 1441):',
        '        numeric_open_time = pd.to_numeric(frame["open_time"], errors="coerce")\n'
        '        valid_open_time = numeric_open_time.notna()\n'
        '        frame = frame.loc[valid_open_time].copy()\n'
        '        frame["open_time"] = numeric_open_time.loc[valid_open_time].astype("int64")\n'
        '        # candidate-15-v4-strict-open-time\n'
        '        if len(frame.index) not in (1439, 1440, 1441):',
        label="strict-open-time",
    )

    # Candidate 14 has already installed generic plan-origin lifecycle routing.
    # Add one portfolio state router and one post-activation engine per market
    # before event cursors are initialized over self.logic.
    initialization = '''            self.session_logic_key = SESSION_LOGIC_KEY
            self.logic[self.session_logic_key] = SessionAuctionBridge(
                session_logic_config,
                str(instruments["BTCUSDT"].id),
                logic_key=self.session_logic_key,
            )
            self.sizer = RiskSizer(logic_config.risk_fraction)'''
    initialization_v4 = '''            self.session_logic_key = SESSION_LOGIC_KEY
            self.logic[self.session_logic_key] = SessionAuctionBridge(
                session_logic_config,
                str(instruments["BTCUSDT"].id),
                logic_key=self.session_logic_key,
            )
            self.initiative_key = QHI_ROUTER_KEY
            self.logic[self.initiative_key] = PersistentQuarterHourRouter(logic_config)
            self.continuation_keys = {
                symbol: f"{symbol}::C15_PERSISTENT_QH_CONTINUATION"
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
        initialization_v4,
        label="persistent-initiative-engines",
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

    # The V3 SCDAM family is not mixed into V4.  It remains alive only as the
    # external-pool observer used by the independent continuation family's target
    # selection.  Every emitted core plan receives a terminal lifecycle record.
    source = _replace(
        source,
        "                leadership = self.leadership.decide(\n",
        '''                self._logic_for_plan(plan, symbol).mark_rejected(
                    plan,
                    ts_ns,
                    "C15_V4_CORE_FAMILY_QUARANTINED",
                    {
                        "candidate15_state": "NO_TRADE",
                        "candidate15_policy": "FAILED_FAMILY_QUARANTINE",
                        "prior_evidence": (
                            "V3 predeclared screen produced one loss from one trade "
                            "and retained only two of seven Candidate 13 reference winners"
                        ),
                    },
                )
                self._capture_events(self._logic_key_for_plan(plan, symbol))
                self.rejections.append({
                    "type": "C15_V4_CORE_FAMILY_QUARANTINED",
                    "observed_ts_ns": plan.observed_ts_ns,
                    "scenario_id": plan.scenario_id,
                    "scenario": plan.scenario.value,
                    "symbol": symbol,
                    "reason": "C15_V4_CORE_FAMILY_QUARANTINED",
                    "net_structural_r": str(plan.net_r),
                })
                continue
                leadership = self.leadership.decide(
''',
        label="core-family-quarantine",
    )

    # Generate only new post-activation legs.  The periodic common-flow event is
    # never itself an entry.  All candidates still compete through the unchanged
    # GlobalCandidateMutex.
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
        label="post-activation-continuation-generation",
    )

    # SESSION_I7 lacks the continuously observed compatible state interface.
    # Preserve observation evidence but fail closed rather than bypass V4.
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
                                    "SESSION_I7 lacks Candidate 15 V4's repeated "
                                    "cross-market initiative state"
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

    # A rejected contingent child after a parent fill must never leave naked
    # exposure.  This remains an engine error and cannot pass safety validation.
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
            # candidate-15-v4-protective-rejection-fail-close
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
        label="protective-order-rejection-fail-close",
    )

    required = {
        "candidate-15-v4-strict-open-time": 1,
        "PersistentQuarterHourRouter(logic_config)": 1,
        "PersistentInitiativeContinuationEngine(": 1,
        "initiative_state = self.logic[self.initiative_key].on_batch(": 1,
        "plans.append((continuation, continuation_candidate))": 1,
        "C15_V4_CORE_FAMILY_QUARANTINED": 3,
        "C15_UNROUTED_SCENARIO_FAMILY": 3,
        "candidate-15-v4-protective-rejection-fail-close": 1,
    }
    bad = {
        token: (source.count(token), expected)
        for token, expected in required.items()
        if source.count(token) != expected
    }
    if bad:
        raise RuntimeError(f"Candidate 15 V4 routes were not materialized: {bad}")
    return source
