"""Materialize the continuous, FAR-only development runner.

This transformer changes research framing and lifecycle semantics, not the
backtest engine. NautilusTrader remains the sole clock, order, fill, fee,
margin, position and NAV engine.

The inherited source is fail-closed transformed to:
- admit SCDAM core FAR plans only (AAC is a separate scenario domain);
- stop opening new decisions at the 28-day boundary without flattening;
- resolve pending orders/positions through a committed data tail;
- close a stale causal episode after one complete 24-hour liquidity cycle;
- classify the run as development evidence, never validation or success.
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
            f"continuous FAR materialization drifted at {label}: "
            f"expected {expected} occurrence(s), found {count}",
        )
    return source.replace(old, new)


def materialize_continuous_far_source(source: str) -> str:
    source = _replace(
        source,
        '''    evaluation_end = date.fromisoformat(selected["end_exclusive"])
    warmup_start = evaluation_start - timedelta(days=int(config["selection"]["warmup_days"]))
    output_dir.mkdir(parents=True, exist_ok=True)
''',
        '''    evaluation_end = date.fromisoformat(selected["end_exclusive"])
    development_contract = config["development_contract"]
    resolution_end = evaluation_end + timedelta(
        days=int(development_contract["resolution_tail_days"])
    )
    # Load one additional operational day so a market close submitted exactly
    # at resolution_end can be acknowledged by NautilusTrader.
    data_end_inclusive = resolution_end
    warmup_start = evaluation_start - timedelta(days=int(config["selection"]["warmup_days"]))
    output_dir.mkdir(parents=True, exist_ok=True)
''',
        label="interval-and-resolution-tail",
    )
    source = _replace(
        source,
        '''        frames[symbol], files = load_symbol_bars(symbol, warmup_start, evaluation_end, output_dir / "data")''',
        '''        frames[symbol], files = load_symbol_bars(
            symbol,
            warmup_start,
            data_end_inclusive,
            output_dir / "data",
        )''',
        label="tail-data-loading",
    )
    source = _replace(
        source,
        '''        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "files": manifest,''',
        '''        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "resolution_end_exclusive": resolution_end.isoformat(),
        "maximum_holding_minutes": int(
            development_contract["maximum_holding_minutes"]
        ),
        "research_stage": "DEVELOPMENT",
        "validation_eligible": False,
        "files": manifest,''',
        label="manifest-research-contract",
    )
    source = _replace(
        source,
        '''    evaluation_start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    evaluation_end_ns = int(pd.Timestamp(evaluation_end, tz="UTC").value)
''',
        '''    evaluation_start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    evaluation_end_ns = int(pd.Timestamp(evaluation_end, tz="UTC").value)
    resolution_end_ns = int(pd.Timestamp(resolution_end, tz="UTC").value)
    max_hold_ns = (
        int(development_contract["maximum_holding_minutes"])
        * 60
        * 1_000_000_000
    )
''',
        label="resolution-nanoseconds",
    )
    source = _replace(
        source,
        '''        evaluation_start_ns: int
        evaluation_end_ns: int
        starting_nav: Decimal''',
        '''        evaluation_start_ns: int
        evaluation_end_ns: int
        resolution_end_ns: int
        max_hold_ns: int
        starting_nav: Decimal''',
        label="strategy-config-contract",
    )
    source = _replace(
        source,
        '''            self.active_plan: TradePlan | None = None
            self.active_symbol: str | None = None
            self.last_ts_ns = 0''',
        '''            self.active_plan: TradePlan | None = None
            self.active_symbol: str | None = None
            self.active_position_opened_ns: int | None = None
            self.time_exit_requested = False
            self.resolution_exit_requested = False
            self.last_ts_ns = 0''',
        label="lifecycle-state",
    )
    source = _replace(
        source,
        '''                    self.mutex.mark_entry_filled(scenario_id)
                    self.logic[self.active_symbol].mark_entry_filled(''',
        '''                    self.mutex.mark_entry_filled(scenario_id)
                    self.active_position_opened_ns = ts_ns
                    self.time_exit_requested = False
                    self.logic[self.active_symbol].mark_entry_filled(''',
        label="position-open-timestamp",
    )
    source = _replace(
        source,
        '''                    self.active_plan = None
                    self.active_symbol = None
            elif self.mutex.state == SlotState.POSITION_OPEN:''',
        '''                    self.active_plan = None
                    self.active_symbol = None
                    self.active_position_opened_ns = None
                    self.time_exit_requested = False
            elif self.mutex.state == SlotState.POSITION_OPEN:''',
        label="pending-terminal-reset",
    )
    source = _replace(
        source,
        '''                    self.active_plan = None
                    self.active_symbol = None

        def _submit(self, plan: TradePlan, candidate: Candidate) -> None:''',
        '''                    self.active_plan = None
                    self.active_symbol = None
                    self.active_position_opened_ns = None
                    self.time_exit_requested = False

        def _enforce_max_hold(self, ts_ns: int) -> None:
            if (
                self.mutex.state != SlotState.POSITION_OPEN
                or self.active_plan is None
                or self.active_symbol is None
                or self.active_position_opened_ns is None
                or self.time_exit_requested
                or ts_ns - self.active_position_opened_ns < self.config.max_hold_ns
            ):
                return
            instrument_id = instruments[self.active_symbol].id
            self.lifecycle.append({
                "type": "SCENARIO_MAX_HOLD_EXIT",
                "ts_event": ts_ns,
                "scenario_id": self.active_plan.scenario_id,
                "symbol": self.active_symbol,
                "opened_ts_ns": self.active_position_opened_ns,
                "maximum_holding_ns": self.config.max_hold_ns,
            })
            if self.cache.orders_open_count(
                instrument_id=instrument_id,
                strategy_id=self.id,
            ):
                self.cancel_all_orders(instrument_id)
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)
            self.time_exit_requested = True

        def _submit(self, plan: TradePlan, candidate: Candidate) -> None:''',
        label="maximum-hold-lifecycle",
    )
    source = _replace(
        source,
        '''                if plan is None:
                    continue
                if ts_ns < self.config.evaluation_start_ns:''',
        '''                if plan is None:
                    continue
                if plan.scenario.value != "FAR":
                    self.logic[symbol].mark_rejected(
                        plan,
                        ts_ns,
                        "DEVELOPMENT_DOMAIN_CORE_FAR_ONLY",
                    )
                    self._capture_events(symbol)
                    self.rejections.append({
                        "type": "SCENARIO_DOMAIN_REJECTED",
                        "observed_ts_ns": plan.observed_ts_ns,
                        "scenario_id": plan.scenario_id,
                        "symbol": symbol,
                        "scenario": plan.scenario.value,
                        "reason": "DEVELOPMENT_DOMAIN_CORE_FAR_ONLY",
                    })
                    continue
                if ts_ns < self.config.evaluation_start_ns:''',
        label="far-only-domain",
    )
    source = _replace(
        source,
        '''        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._release_if_terminal(self.last_ts_ns, "BAR_TERMINAL_SYNC")
            if self.last_ts_ns >= self.config.evaluation_end_ns:
                if self.buffer_ts is not None and len(self.buffer) == len(SYMBOLS):
                    self._process_batch(self.buffer_ts)
                    self.buffer.clear()
                    self.buffer_ts = None
                self._flatten()
                return
            symbol = self._symbol(bar)''',
        '''        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._release_if_terminal(self.last_ts_ns, "BAR_TERMINAL_SYNC")
            self._enforce_max_hold(self.last_ts_ns)

            if self.last_ts_ns >= self.config.resolution_end_ns:
                if not self.resolution_exit_requested:
                    if not self._all_flat() or self._open_orders() > 0:
                        self.errors.append({
                            "type": "RESOLUTION_TAIL_UNRESOLVED",
                            "ts_ns": self.last_ts_ns,
                            "active_scenario_id": (
                                None
                                if self.active_plan is None
                                else self.active_plan.scenario_id
                            ),
                            "active_symbol": self.active_symbol,
                        })
                        self._flatten()
                    self.resolution_exit_requested = True
                return

            # The decision window is closed. Existing GTD parents, protective
            # orders and positions remain under NautilusTrader through the
            # committed tail, but no detector receives new observations.
            if self.last_ts_ns >= self.config.evaluation_end_ns:
                self.buffer.clear()
                self.buffer_ts = None
                return

            symbol = self._symbol(bar)''',
        label="decision-boundary-without-flatten",
    )
    source = _replace(
        source,
        '''        def on_stop(self) -> None:
            self._flatten()''',
        '''        def on_stop(self) -> None:
            if not self._all_flat() or self._open_orders() > 0:
                self.errors.append({
                    "type": "ENGINE_STOP_UNRESOLVED_POSITION_OR_ORDER",
                    "ts_ns": self.last_ts_ns,
                    "active_scenario_id": (
                        None if self.active_plan is None else self.active_plan.scenario_id
                    ),
                    "active_symbol": self.active_symbol,
                })
            self._flatten()''',
        label="engine-stop-fail-closed",
    )
    source = _replace(
        source,
        '''        evaluation_start_ns=evaluation_start_ns,
        evaluation_end_ns=evaluation_end_ns,
        starting_nav=starting_nav,''',
        '''        evaluation_start_ns=evaluation_start_ns,
        evaluation_end_ns=evaluation_end_ns,
        resolution_end_ns=resolution_end_ns,
        max_hold_ns=max_hold_ns,
        starting_nav=starting_nav,''',
        label="strategy-instantiation-contract",
    )
    source = _replace(
        source,
        '''        "candidate": "candidate-11-market-leadership-scdam",
        "evidence_class": "NAUTILUS_ACCOUNT_NAV",''',
        '''        "candidate": "candidate-11-core-far-transfer",
        "research_stage": "DEVELOPMENT",
        "validation_eligible": False,
        "evidence_class": "NAUTILUS_ACCOUNT_NAV",''',
        label="metric-stage-classification",
    )
    source = _replace(
        source,
        '''        "success_claim": False,''',
        '''        "scenario_max_hold_exit_count": sum(
            item.get("type") == "SCENARIO_MAX_HOLD_EXIT"
            for item in lifecycle
        ),
        "resolution_tail_unresolved_count": sum(
            item.get("type") == "RESOLUTION_TAIL_UNRESOLVED"
            for item in errors
        ),
        "success_claim": False,''',
        label="lifecycle-metrics",
    )

    markers = (
        "DEVELOPMENT_DOMAIN_CORE_FAR_ONLY",
        "SCENARIO_MAX_HOLD_EXIT",
        "RESOLUTION_TAIL_UNRESOLVED",
        '"research_stage": "DEVELOPMENT"',
    )
    for marker in markers:
        if source.count(marker) < 1:
            raise RuntimeError(f"continuous FAR marker was not installed: {marker}")
    if "if self.last_ts_ns >= self.config.evaluation_end_ns:\n                if self.buffer_ts" in source:
        raise RuntimeError("weekly boundary flatten branch remains")
    return source
