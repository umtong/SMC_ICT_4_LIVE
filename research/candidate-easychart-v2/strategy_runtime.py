"""Market-data and four-symbol arbitration methods for EasyChart v2."""
from __future__ import annotations

from typing import Any

from nautilus_trader.model.data import Bar

from model import Candle, EasyChartStateEngine, EngineConfig, Side


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
        instrument_id = self.signal_to_instrument.get(bar.bar_type)
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
                self._record(
                    "plan",
                    plan_id=plan.plan_id,
                    causal_event_id=plan.causal_event_id,
                    symbol=plan.symbol,
                    family=plan.family.value,
                    side=plan.side.name,
                    entry=plan.entry,
                    stop=plan.stop,
                    target=plan.target,
                    gross_rr=plan.gross_rr,
                    source_span=plan.source_span,
                    source_prominence_atr=plan.source_prominence_atr,
                )
        if len(self.signal_bucket_seen) == len(self.config.instrument_ids):
            self._flush_signal_bucket()

    def _flush_signal_bucket(self) -> None:
        if self.signal_bucket_plans and self.active_plan is None and self._portfolio_flat():
            instrument_id, plan = sorted(
                self.signal_bucket_plans,
                key=lambda item: (
                    -item[1].source_span,
                    -item[1].source_prominence_atr,
                    item[1].symbol,
                    item[1].plan_id,
                ),
            )[0]
            self._submit_plan(instrument_id, plan)
        self.signal_bucket_seen.clear()
        self.signal_bucket_plans.clear()
        self.signal_bucket_ts = None
