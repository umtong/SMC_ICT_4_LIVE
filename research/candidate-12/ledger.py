"""Causal multi-timeframe liquidity-pool ledger."""
from __future__ import annotations

import math
from statistics import median
from typing import Any, Iterable

from smc_ict_4.contracts import ResearchEvent
from model import BarObs, LiquidityPool, NS_DAY, NS_MINUTE, Side, _AggBar


class LiquidityLedgerMixin:
        def _emit(
            self,
            *,
            scenario_id: str,
            event_type: str,
            event_time_ns: int,
            observed_time_ns: int,
            next_state: str,
            reason_code: str,
            reference_price: float | None = None,
            details: dict[str, Any] | None = None,
        ) -> None:
            previous = self._states.get(scenario_id, "NONE")
            event = ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=self.instrument_id,
                event_type=event_type,
                event_time_ns=int(event_time_ns),
                observed_time_ns=int(observed_time_ns),
                previous_state=previous,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=None if reference_price is None else f"{reference_price:.12f}".rstrip("0").rstrip("."),
                details=details or {},
            )
            self.events.append(event)
            self._states[scenario_id] = next_state

        def _atr(self) -> float | None:
            if len(self._true_ranges) < self.config.atr_period:
                return None
            return sum(self._true_ranges) / len(self._true_ranges)

        def _relative_volume(self, volume: float) -> float:
            if len(self._volumes) < max(10, self.config.volume_period // 4):
                return 1.0
            baseline = median(self._volumes)
            return volume / baseline if baseline > 0 else 1.0

        def _flow_threshold(self, floor: float) -> float:
            if len(self._abs_flows) < max(10, self.config.flow_period // 4):
                return floor
            # Median absolute aggressor imbalance adapts to instrument/session while
            # retaining a small fixed lower bound to reject noise.
            return max(floor, 0.50 * median(self._abs_flows))

        @staticmethod
        def _aggregate(bars: Iterable[BarObs]) -> _AggBar:
            values = list(bars)
            if not values:
                raise ValueError("cannot aggregate an empty bar sequence")
            return _AggBar(
                ts_ns=values[-1].ts_ns,
                open=values[0].open,
                high=max(bar.high for bar in values),
                low=min(bar.low for bar in values),
                close=values[-1].close,
                volume=sum(bar.volume for bar in values),
            )

        def _new_pool(
            self,
            *,
            side: Side,
            price: float,
            source: str,
            event_time_ns: int,
            observed_time_ns: int,
            atr: float,
        ) -> None:
            if not math.isfinite(price) or price <= 0:
                return
            merge_distance = self.config.pool_merge_atr * atr
            candidates = [
                pool for pool in self._pools
                if pool.active and pool.side is side and abs(pool.price - price) <= merge_distance
            ]
            if candidates:
                # Keep the older causal identity and strengthen it with the fresh
                # independent observation; use the more external extreme.
                pool = min(candidates, key=lambda item: item.observed_time_ns)
                pool.price = max(pool.price, price) if side is Side.HIGH else min(pool.price, price)
                pool.expires_time_ns = max(
                    pool.expires_time_ns,
                    observed_time_ns + self.config.pool_expiry_minutes * NS_MINUTE,
                )
                pool.touches += 1
                self._emit(
                    scenario_id=pool.pool_id,
                    event_type="LIQUIDITY_POOL_REINFORCED",
                    event_time_ns=event_time_ns,
                    observed_time_ns=observed_time_ns,
                    next_state="LIVE",
                    reason_code=f"{source}_COINCIDES_WITH_LIVE_POOL",
                    reference_price=pool.price,
                    details={"source": source, "touches": pool.touches},
                )
                return

            pool_id = f"{self.instrument_id}-POOL-{source}-{side.value}-{event_time_ns}"
            pool = LiquidityPool(
                pool_id=pool_id,
                side=side,
                price=price,
                source=source,
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                expires_time_ns=observed_time_ns + self.config.pool_expiry_minutes * NS_MINUTE,
                formed_bar_index=self._bar_index,
            )
            self._pools.append(pool)
            self.pool_counts[source] += 1
            self._emit(
                scenario_id=pool_id,
                event_type="LIQUIDITY_POOL_FORMED",
                event_time_ns=event_time_ns,
                observed_time_ns=observed_time_ns,
                next_state="LIVE",
                reason_code=f"CAUSAL_{source}_EXTREME",
                reference_price=price,
                details={"side": side.value, "source": source},
            )
            self._prune_pool_count(side, observed_time_ns)

        def _prune_pool_count(self, side: Side, observed_time_ns: int) -> None:
            live = [pool for pool in self._pools if pool.active and pool.side is side]
            overflow = len(live) - self.config.max_pools_per_side
            if overflow <= 0:
                return
            for pool in sorted(live, key=lambda item: item.observed_time_ns)[:overflow]:
                self._deactivate_pool(pool, observed_time_ns, "POOL_LEDGER_CAP_OLDEST")

        def _deactivate_pool(self, pool: LiquidityPool, ts_ns: int, reason: str) -> None:
            if not pool.active:
                return
            pool.active = False
            self._emit(
                scenario_id=pool.pool_id,
                event_type="LIQUIDITY_POOL_TERMINAL",
                event_time_ns=ts_ns,
                observed_time_ns=ts_ns,
                next_state="TERMINAL",
                reason_code=reason,
                reference_price=pool.price,
                details={"touches": pool.touches, "source": pool.source},
            )

        def _expire_pools(self, ts_ns: int) -> None:
            protected = {self._probe.pool_id if self._probe else None, self._confirmation.pool_id if self._confirmation else None}
            for pool in self._pools:
                if pool.active and pool.pool_id not in protected and ts_ns >= pool.expires_time_ns:
                    self._deactivate_pool(pool, ts_ns, "TIME_EXPIRY")

        def _pool_by_id(self, pool_id: str) -> LiquidityPool | None:
            return next((pool for pool in self._pools if pool.pool_id == pool_id), None)

        def _update_internal_pivots(self, bar: BarObs) -> None:
            self._internal_window.append(bar)
            wing = self.config.internal_pivot_wing
            if len(self._internal_window) < 2 * wing + 1:
                return
            window = list(self._internal_window)
            candidate = window[wing]
            others = window[:wing] + window[wing + 1 :]
            if candidate.high > max(item.high for item in others):
                self._latest_internal_high = (candidate.high, candidate.ts_ns)
            if candidate.low < min(item.low for item in others):
                self._latest_internal_low = (candidate.low, candidate.ts_ns)

        def _update_external_structures(self, bar: BarObs, atr: float) -> None:
            external_size = self.config.external_tf_minutes
            external_bucket = (bar.ts_ns - 1) // (external_size * NS_MINUTE)
            if self._last_external_bucket is None:
                self._last_external_bucket = external_bucket
            if external_bucket != self._last_external_bucket:
                completed = [item for item in self._bars if (item.ts_ns - 1) // (external_size * NS_MINUTE) == self._last_external_bucket]
                if completed:
                    aggregate = self._aggregate(completed)
                    self._external_bars.append(aggregate)
                    self._confirm_external_pivot(atr, bar.ts_ns)
                self._last_external_bucket = external_bucket

            range_size = self.config.range_tf_minutes
            range_bucket = (bar.ts_ns - 1) // (range_size * NS_MINUTE)
            if self._last_range_bucket is None:
                self._last_range_bucket = range_bucket
            if range_bucket != self._last_range_bucket:
                completed = [item for item in self._bars if (item.ts_ns - 1) // (range_size * NS_MINUTE) == self._last_range_bucket]
                if completed:
                    aggregate = self._aggregate(completed)
                    self._new_pool(
                        side=Side.HIGH,
                        price=aggregate.high,
                        source="PRIOR_4H",
                        event_time_ns=aggregate.ts_ns,
                        observed_time_ns=bar.ts_ns,
                        atr=atr,
                    )
                    self._new_pool(
                        side=Side.LOW,
                        price=aggregate.low,
                        source="PRIOR_4H",
                        event_time_ns=aggregate.ts_ns,
                        observed_time_ns=bar.ts_ns,
                        atr=atr,
                    )
                self._last_range_bucket = range_bucket

            day_bucket = (bar.ts_ns - 1) // NS_DAY
            if self._last_day_bucket is None:
                self._last_day_bucket = day_bucket
            if day_bucket != self._last_day_bucket:
                completed = [item for item in self._bars if (item.ts_ns - 1) // NS_DAY == self._last_day_bucket]
                if completed:
                    aggregate = self._aggregate(completed)
                    self._new_pool(
                        side=Side.HIGH,
                        price=aggregate.high,
                        source="PRIOR_DAY",
                        event_time_ns=aggregate.ts_ns,
                        observed_time_ns=bar.ts_ns,
                        atr=atr,
                    )
                    self._new_pool(
                        side=Side.LOW,
                        price=aggregate.low,
                        source="PRIOR_DAY",
                        event_time_ns=aggregate.ts_ns,
                        observed_time_ns=bar.ts_ns,
                        atr=atr,
                    )
                self._last_day_bucket = day_bucket

        def _confirm_external_pivot(self, atr: float, observed_time_ns: int) -> None:
            wing = self.config.external_pivot_wing
            if len(self._external_bars) < 2 * wing + 1:
                return
            window = list(self._external_bars)
            candidate = window[wing]
            others = window[:wing] + window[wing + 1 :]
            if candidate.high > max(item.high for item in others):
                self._new_pool(
                    side=Side.HIGH,
                    price=candidate.high,
                    source="CONFIRMED_15M_PIVOT",
                    event_time_ns=candidate.ts_ns,
                    observed_time_ns=observed_time_ns,
                    atr=atr,
                )
            if candidate.low < min(item.low for item in others):
                self._new_pool(
                    side=Side.LOW,
                    price=candidate.low,
                    source="CONFIRMED_15M_PIVOT",
                    event_time_ns=candidate.ts_ns,
                    observed_time_ns=observed_time_ns,
                    atr=atr,
                )

        def _eligible_crossed_pool(self, bar: BarObs, atr: float) -> LiquidityPool | None:
            previous = self._previous_close
            if previous is None:
                return None
            candidates: list[tuple[float, int, LiquidityPool]] = []
            source_rank = {"PRIOR_DAY": 0, "PRIOR_4H": 1, "CONFIRMED_15M_PIVOT": 2}
            for pool in self._pools:
                if not pool.active or self._bar_index - pool.formed_bar_index < self.config.min_pool_age_bars:
                    continue
                if pool.side is Side.HIGH:
                    penetration = (bar.high - pool.price) / atr
                    approached = previous <= pool.price + 0.05 * atr
                else:
                    penetration = (pool.price - bar.low) / atr
                    approached = previous >= pool.price - 0.05 * atr
                if not approached or penetration < self.config.probe_min_atr or penetration > self.config.probe_max_atr:
                    continue
                distance = abs(pool.price - previous) / atr
                candidates.append((distance, source_rank.get(pool.source, 9), pool))
            if not candidates:
                return None
            candidates.sort(key=lambda value: (value[0], value[1], value[2].observed_time_ns))
            return candidates[0][2]
