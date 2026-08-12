"""Market-data and four-symbol arbitration methods for EasyChart v2."""
from __future__ import annotations

from typing import Any

from nautilus_trader.model.data import Bar

from model import Candle, EasyChartStateEngine, EngineConfig, Side, TradePlan


class EasyChartRuntimeMixin:
    def on_start(self) -> None:
        for instrument_id, signal_type, execution_type in zip(
            self.config.instrument_ids,
            self.config.signal_bar_types,
            self.config.execution_bar_types,
            strict=True,
        ):
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                self.log.error(f"Could not find instrument for {instrument_id}")
                self.stop()
                return
            self.instruments[instrument_id] = instrument
            self.engines[instrument_id] = EasyChartStateEngine(
                symbol=instrument.raw_symbol.value,
                config=EngineConfig(
                    pivot_spans=self.config.pivot_spans,
                    min_prominence_atr=self.config.min_prominence_atr,
                    min_gross_rr=self.config.min_gross_rr,
                    tick_size=float(instrument.price_increment),
                    enable_rejection=self.config.enable_rejection,
                    enable_acceptance=self.config.enable_acceptance,
                ),
            )
            self.subscribe_bars(execution_type)
            self.subscribe_bars(signal_type)

    def _record(self, kind: str, **values: Any) -> None:
        self.event_log.append({"kind": kind, "ts_ns": self.clock.timestamp_ns(), **values})

    def _portfolio_flat(self) -> bool:
        return all(self.portfolio.is_flat(item) for item in self.config.instrument_ids)

    def _plan_values(self, plan: TradePlan) -> dict[str, Any]:
        return {
            "plan_id": plan.plan_id,
            "causal_event_id": plan.causal_event_id,
            "symbol": plan.symbol,
            "family": plan.family.value,
            "side": plan.side.name,
            "observed_time_ns": plan.observed_time_ns,
            "entry": plan.entry,
            "stop": plan.stop,
            "target": plan.target,
            "gross_rr": plan.gross_rr,
            "source_boundary_id": plan.source_boundary_id,
            "source_level": plan.source_level,
            "source_event_time_ns": plan.source_event_time_ns,
            "source_observed_time_ns": plan.source_observed_time_ns,
            "source_span": plan.source_span,
            "source_prominence_atr": plan.source_prominence_atr,
            "target_boundary_id": plan.target_boundary_id,
            "target_event_time_ns": plan.target_event_time_ns,
            "target_observed_time_ns": plan.target_observed_time_ns,
            "target_span": plan.target_span,
            "target_prominence_atr": plan.target_prominence_atr,
            "interaction_index": plan.interaction_index,
            "confirmation_index": plan.confirmation_index,
            "interaction_time_ns": plan.interaction_time_ns,
            "confirmation_time_ns": plan.confirmation_time_ns,
            "trigger_extreme": plan.trigger_extreme,
            "origin_boundary_id": plan.origin_boundary_id,
            "origin_level": plan.origin_level,
        }

    def _pending_plan_invalidated(self, bar: Bar) -> bool:
        if self.active_plan is None or self.active_instrument_id != bar.bar_type.instrument_id:
            return False
        if not self.portfolio.is_flat(self.active_instrument_id):
            return False
        plan = self.active_plan
        if plan.side is Side.LONG:
            return float(bar.low) <= plan.stop or float(bar.high) >= plan.target
        return float(bar.high) >= plan.stop or float(bar.low) <= plan.target

    def _cancel_spent_pending_plan(self, bar: Bar) -> None:
        if not self._pending_plan_invalidated(bar) or self.entry_cancel_requested:
            return
        plan = self.active_plan
        assert plan is not None
        self.entry_cancel_requested = True
        self.cancel_all_orders(self.active_instrument_id)
        self._record("pending_canceled_causal_end", plan_id=plan.plan_id)

    def on_bar(self, bar: Bar) -> None:
        # Every external 1m bar can terminate an unfilled plan. Signal logic is
        # evaluated only on NautilusTrader's internally aggregated 5m bars.
        self._cancel_spent_pending_plan(bar)
        instrument_id = self.signal_to_instrument.get(bar.bar_type.id_spec_key())
        if instrument_id is None:
            return

        if self.signal_bucket_ts is None:
            self.signal_bucket_ts = bar.ts_event
        elif bar.ts_event != self.signal_bucket_ts:
            self._flush_signal_bucket()
            self.signal_bucket_ts = bar.ts_event

        plans = self.engines[instrument_id].on_bar(
            Candle(
                ts_close_ns=bar.ts_event,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
            ),
        )
        self.signal_bucket_seen.add(instrument_id)
        if bar.ts_event >= self.config.trading_start_ns:
            for plan in plans:
                self.plan_log[plan.plan_id] = plan
                self.signal_bucket_plans.append((instrument_id, plan))
                self._record("plan", **self._plan_values(plan))
        if len(self.signal_bucket_seen) == len(self.config.instrument_ids):
            self._flush_signal_bucket()

    def _flush_signal_bucket(self) -> None:
        ranked = sorted(
            self.signal_bucket_plans,
            key=lambda item: (
                -item[1].source_span,
                -item[1].source_prominence_atr,
                item[1].symbol,
                item[1].plan_id,
            ),
        )
        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, (instrument_id, plan) in enumerate(ranked, start=1):
                    self._record(
                        "plan_skipped_global_slot",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                    )
            else:
                selected_index: int | None = None
                for index, (instrument_id, plan) in enumerate(ranked):
                    if self._submit_plan(instrument_id, plan):
                        selected_index = index
                        self._record(
                            "arbitration_selected",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                        )
                        break
                if selected_index is not None:
                    for rank, (instrument_id, plan) in enumerate(ranked, start=1):
                        if rank - 1 == selected_index:
                            continue
                        if rank - 1 < selected_index:
                            # Earlier plans were not executable and already have a
                            # quantity rejection event, not an arbitration loss.
                            continue
                        self._record(
                            "plan_skipped_arbitration",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=ranked[selected_index][1].plan_id,
                        )
        self.signal_bucket_seen.clear()
        self.signal_bucket_plans.clear()
        self.signal_bucket_ts = None
