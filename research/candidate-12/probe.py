"""Probe classification: liquidity rejection versus acceptance."""
from __future__ import annotations

from model import BarObs, ConfirmationState, Direction, ProbeState, ScenarioKind, Side


class ProbeClassificationMixin:
        def _start_probe(self, pool: LiquidityPool, bar: BarObs, relative_volume: float) -> None:
            self._scenario_counter += 1
            scenario_id = f"{self.instrument_id}-CLA-{self._scenario_counter:06d}"
            extreme = bar.high if pool.side is Side.HIGH else bar.low
            outside = int(bar.close > pool.price) if pool.side is Side.HIGH else int(bar.close < pool.price)
            self._probe = ProbeState(
                scenario_id=scenario_id,
                pool_id=pool.pool_id,
                side=pool.side,
                started_index=self._bar_index,
                started_ts_ns=bar.ts_ns,
                extreme=extreme,
                sweep_flow=bar.signed_flow,
                relative_volume=relative_volume,
                closes_outside=outside,
            )
            pool.claimed = True
            pool.touches += 1
            self._emit(
                scenario_id=scenario_id,
                event_type="LIQUIDITY_POOL_PROBED",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                next_state="PROBED",
                reason_code="PRICE_CROSSED_LIVE_EXTERNAL_LIQUIDITY",
                reference_price=pool.price,
                details={
                    "pool_id": pool.pool_id,
                    "pool_side": pool.side.value,
                    "pool_source": pool.source,
                    "sweep_flow": bar.signed_flow,
                    "relative_volume": relative_volume,
                    "extreme": extreme,
                },
            )

        def _probe_rejection(self, probe: ProbeState, pool: LiquidityPool, bar: BarObs, atr: float) -> bool:
            flow_floor = self._flow_threshold(self.config.absorption_flow_min)
            opposite_floor = self._flow_threshold(self.config.opposite_flow_min)
            if probe.side is Side.HIGH:
                reclaimed = bar.close <= pool.price - self.config.rejection_reclaim_atr * atr
                close_location_ok = bar.close_location <= self.config.rejection_close_location
                absorption = probe.sweep_flow >= flow_floor
                opposite_impulse = bar.signed_flow <= -opposite_floor
            else:
                reclaimed = bar.close >= pool.price + self.config.rejection_reclaim_atr * atr
                close_location_ok = bar.close_location >= 1.0 - self.config.rejection_close_location
                absorption = probe.sweep_flow <= -flow_floor
                opposite_impulse = bar.signed_flow >= opposite_floor
            return reclaimed and close_location_ok and (absorption or opposite_impulse)

        def _probe_acceptance(self, probe: ProbeState, pool: LiquidityPool, bar: BarObs, atr: float, rel_volume: float) -> bool:
            flow_floor = self._flow_threshold(self.config.acceptance_flow_min)
            if probe.side is Side.HIGH:
                outside = bar.close >= pool.price + self.config.acceptance_close_atr * atr
                body_direction = bar.close > bar.open
                close_location_ok = bar.close_location >= self.config.acceptance_close_location
                flow_ok = bar.signed_flow >= flow_floor
            else:
                outside = bar.close <= pool.price - self.config.acceptance_close_atr * atr
                body_direction = bar.close < bar.open
                close_location_ok = bar.close_location <= 1.0 - self.config.acceptance_close_location
                flow_ok = bar.signed_flow <= -flow_floor
            return (
                probe.closes_outside >= self.config.acceptance_min_closes
                and outside
                and body_direction
                and bar.body >= self.config.acceptance_body_atr * atr
                and close_location_ok
                and flow_ok
                and rel_volume >= self.config.min_probe_relative_volume
            )

        def _advance_probe(self, bar: BarObs, atr: float, rel_volume: float) -> None:
            probe = self._probe
            if probe is None:
                return
            pool = self._pool_by_id(probe.pool_id)
            if pool is None or not pool.active:
                self._terminate_probe(bar.ts_ns, "POOL_NOT_LIVE")
                return
            probe.extreme = max(probe.extreme, bar.high) if probe.side is Side.HIGH else min(probe.extreme, bar.low)
            if (probe.side is Side.HIGH and bar.close > pool.price) or (probe.side is Side.LOW and bar.close < pool.price):
                probe.closes_outside += 1
            else:
                probe.closes_outside = 0

            if self._probe_rejection(probe, pool, bar, atr):
                direction = Direction.SHORT if probe.side is Side.HIGH else Direction.LONG
                structure = self._latest_internal_low[0] if direction is Direction.SHORT and self._latest_internal_low else None
                if direction is Direction.LONG:
                    structure = self._latest_internal_high[0] if self._latest_internal_high else None
                self._confirmation = ConfirmationState(
                    scenario_id=probe.scenario_id,
                    pool_id=pool.pool_id,
                    kind=ScenarioKind.REJECTION,
                    direction=direction,
                    started_index=self._bar_index,
                    trigger_extreme=probe.extreme,
                    structure_level=structure,
                )
                self.scenario_counts[ScenarioKind.REJECTION.value] += 1
                self._emit(
                    scenario_id=probe.scenario_id,
                    event_type="LIQUIDITY_REJECTION_CONFIRMED",
                    event_time_ns=probe.started_ts_ns,
                    observed_time_ns=bar.ts_ns,
                    next_state="WAIT_MSS",
                    reason_code="SWEEP_RECLAIM_WITH_ABSORPTION_OR_COUNTERFLOW",
                    reference_price=pool.price,
                    details={
                        "direction": direction.value,
                        "trigger_extreme": probe.extreme,
                        "structure_level": structure,
                        "sweep_flow": probe.sweep_flow,
                        "confirmation_flow": bar.signed_flow,
                    },
                )
                self._probe = None
                return

            if self._probe_acceptance(probe, pool, bar, atr, rel_volume):
                direction = Direction.LONG if probe.side is Side.HIGH else Direction.SHORT
                self._confirmation = ConfirmationState(
                    scenario_id=probe.scenario_id,
                    pool_id=pool.pool_id,
                    kind=ScenarioKind.ACCEPTANCE,
                    direction=direction,
                    started_index=self._bar_index,
                    trigger_extreme=probe.extreme,
                    structure_level=pool.price,
                )
                self.scenario_counts[ScenarioKind.ACCEPTANCE.value] += 1
                self._emit(
                    scenario_id=probe.scenario_id,
                    event_type="LIQUIDITY_ACCEPTANCE_CONFIRMED",
                    event_time_ns=probe.started_ts_ns,
                    observed_time_ns=bar.ts_ns,
                    next_state="WAIT_RETEST",
                    reason_code="MULTI_CLOSE_DISPLACEMENT_AND_AGGRESSOR_ALIGNMENT",
                    reference_price=pool.price,
                    details={
                        "direction": direction.value,
                        "closes_outside": probe.closes_outside,
                        "trigger_extreme": probe.extreme,
                        "confirmation_flow": bar.signed_flow,
                        "relative_volume": rel_volume,
                    },
                )
                self._probe = None
                return

            if self._bar_index - probe.started_index >= self.config.probe_expiry_bars:
                self._terminate_probe(bar.ts_ns, "PROBE_NEITHER_REJECTED_NOR_ACCEPTED")

        def _terminate_probe(self, ts_ns: int, reason: str) -> None:
            probe = self._probe
            if probe is None:
                return
            self.skips[reason] += 1
            self._emit(
                scenario_id=probe.scenario_id,
                event_type="SCENARIO_INVALIDATED",
                event_time_ns=ts_ns,
                observed_time_ns=ts_ns,
                next_state="TERMINAL",
                reason_code=reason,
                details={"pool_id": probe.pool_id},
            )
            pool = self._pool_by_id(probe.pool_id)
            if pool is not None:
                self._deactivate_pool(pool, ts_ns, "LIQUIDITY_INTERACTION_CLASSIFICATION_FAILED")
            self._probe = None
