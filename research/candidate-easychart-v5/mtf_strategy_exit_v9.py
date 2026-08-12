"""Source-backed opposing 15-minute order-block exit for EasyChart v9.

The supplied material states that a newly formed opposing order block can erase
the original directional premise and is a valid reason to close or partially
realize the position. This module chooses the source's full-exit option for a
controlled ablation. It does not score setups, predict outcomes, or add a price
threshold.

Only an order block observed after the first real entry fill can act. The exit
uses one full reduce-only market order, while NautilusTrader owns cancellation,
fill, fee, position, and account state. A state mismatch fails closed.
"""
from __future__ import annotations

from nautilus_trader.common.events import TimeEvent
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled, PositionClosed
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId

from domain import Side
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from event_footprints_v5 import EventLocalZoneDetector
from mtf_strategy_day_v7 import EasyChartDayTradeStrategy, EasyChartMTFConfig


OPPOSING_OB_EXIT_PROVENANCE = (
    "SOURCE_EXPLICIT_OPTION:"
    "NEW_OPPOSING_ORDER_BLOCK_FULLY_EXITS_THE_ACTIVE_POSITION"
)


def is_opposing_order_block(plan_side: Side, zone: PriceZone) -> bool:
    """Return whether ``zone`` is a newly observed premise-opposing OB."""
    if zone.kind is not ZoneKind.ORDER_BLOCK:
        return False
    wanted = ZoneSide.RESISTANCE if plan_side is Side.LONG else ZoneSide.SUPPORT
    return zone.side is wanted


class OpposingOrderBlockExitStrategy(EasyChartDayTradeStrategy):
    """Day-trade strategy with one source-explicit opposing-OB terminal exit."""

    OPPOSING_TIMEFRAME_MINUTES = 15

    def __init__(self, config: EasyChartMTFConfig) -> None:
        super().__init__(config)
        self._opposing_detectors: dict[InstrumentId, EventLocalZoneDetector] = {}
        self._opposing_exit_requested = False
        self._opposing_exit_order_id: ClientOrderId | None = None
        self._opposing_exit_zone_id: str | None = None

    def on_start(self) -> None:
        super().on_start()
        # Component state is STARTING while on_start executes, so is_running is
        # intentionally not used here. A failed base initialization leaves the
        # instrument map incomplete and the detector layer remains disabled.
        if len(self.instruments) != len(self.config.instrument_ids):
            return
        self._opposing_detectors = {
            instrument_id: EventLocalZoneDetector(
                self.instruments[instrument_id].raw_symbol.value,
                self.OPPOSING_TIMEFRAME_MINUTES,
                float(self.instruments[instrument_id].price_increment),
            )
            for instrument_id in self.config.instrument_ids
        }

    def _reset_opposing_exit(self) -> None:
        self._opposing_exit_requested = False
        self._opposing_exit_order_id = None
        self._opposing_exit_zone_id = None

    def _on_max_hold_alert(self, event: TimeEvent) -> None:
        # Mutual exclusion between two terminal exits. The first causal reason
        # to request a full close owns the order; the other becomes redundant.
        if self._opposing_exit_requested:
            return
        super()._on_max_hold_alert(event)

    def _request_opposing_exit(
        self,
        instrument_id: InstrumentId,
        zone: PriceZone,
        event_time_ns: int,
    ) -> None:
        if self.active_plan is None or self.active_instrument_id != instrument_id:
            return
        if self._opposing_exit_requested or self._time_exit_requested:
            return
        if self.emergency_exit_requested or self.portfolio.is_flat(instrument_id):
            return
        if self._first_entry_fill_ts_ns is None:
            return
        if zone.observed_time_ns <= self._first_entry_fill_ts_ns:
            return
        if not is_opposing_order_block(self.active_plan.side, zone):
            return

        positions = self.cache.positions_open(
            instrument_id=instrument_id,
            strategy_id=self.id,
        )
        if len(positions) != 1:
            self._record(
                "emergency_exit_opposing_ob_state_mismatch",
                plan_id=self.active_plan.plan_id,
                instrument_id=str(instrument_id),
                opposing_zone_id=zone.zone_id,
                open_strategy_positions=len(positions),
                event_time_ns=event_time_ns,
                provenance=OPPOSING_OB_EXIT_PROVENANCE,
            )
            self.emergency_exit_requested = True
            self.cancel_all_orders(instrument_id)
            self.close_all_positions(instrument_id)
            return

        position = positions[0]
        order_side = OrderSide.SELL if self.active_plan.side is Side.LONG else OrderSide.BUY
        plan_tag = f"PLAN:{self.active_plan.plan_id}"
        order = self.order_factory.market(
            instrument_id=instrument_id,
            order_side=order_side,
            quantity=position.quantity,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            tags=[
                plan_tag,
                "ROLE:OPPOSING_15M_OB_EXIT",
                "POLICY:FULL_POSITION_EXIT",
                OPPOSING_OB_EXIT_PROVENANCE,
            ],
        )
        self._opposing_exit_requested = True
        self._opposing_exit_order_id = order.client_order_id
        self._opposing_exit_zone_id = zone.zone_id
        self.cancel_all_orders(instrument_id)
        self.submit_order(order, position_id=position.id)
        self._record(
            "opposing_ob_exit_submitted",
            plan_id=self.active_plan.plan_id,
            instrument_id=str(instrument_id),
            position_id=str(position.id),
            client_order_id=str(order.client_order_id),
            opposing_zone_id=zone.zone_id,
            opposing_zone_side=zone.side.value,
            opposing_zone_lower=zone.lower,
            opposing_zone_upper=zone.upper,
            opposing_zone_strength_ratio=zone.strength_ratio,
            opposing_zone_observed_time_ns=zone.observed_time_ns,
            first_entry_fill_ts_ns=self._first_entry_fill_ts_ns,
            event_time_ns=event_time_ns,
            quantity=str(position.quantity),
            provenance=OPPOSING_OB_EXIT_PROVENANCE,
        )

    def _observe_opposing_order_blocks(self) -> None:
        if self.bar_bucket_ts is None:
            return
        for instrument_id, timeframe, bar in sorted(
            self.bar_bucket,
            key=lambda item: (-item[1], str(item[0])),
        ):
            if timeframe != self.OPPOSING_TIMEFRAME_MINUTES:
                continue
            detector = self._opposing_detectors.get(instrument_id)
            if detector is None:
                raise RuntimeError(f"opposing OB detector unavailable for {instrument_id}")
            created = detector.on_bar(self._candle(bar))
            if bar.ts_event < self.config.trading_start_ns:
                continue
            for zone in created:
                if self.active_plan is None:
                    break
                if is_opposing_order_block(self.active_plan.side, zone):
                    self._request_opposing_exit(instrument_id, zone, bar.ts_event)
                    break

    def _flush_bar_bucket(self) -> None:
        # Read the completed 15-minute bar first. Any resulting market exit is
        # submitted at this bar close and can execute only on subsequent data.
        self._observe_opposing_order_blocks()
        super()._flush_bar_bucket()

    def on_order_filled(self, event: OrderFilled) -> None:
        is_opposing_exit = (
            self._opposing_exit_order_id is not None
            and event.client_order_id == self._opposing_exit_order_id
        )
        plan_id = None if self.active_plan is None else self.active_plan.plan_id
        zone_id = self._opposing_exit_zone_id
        super().on_order_filled(event)
        if is_opposing_exit:
            self._record(
                "opposing_ob_exit_filled",
                plan_id=plan_id,
                opposing_zone_id=zone_id,
                client_order_id=str(event.client_order_id),
                instrument_id=str(event.instrument_id),
                last_qty=str(event.last_qty),
                last_px=str(event.last_px),
                commission=str(event.commission),
                event_ts_ns=event.ts_event,
                provenance=OPPOSING_OB_EXIT_PROVENANCE,
            )

    def on_position_closed(self, event: PositionClosed) -> None:
        self._reset_opposing_exit()
        super().on_position_closed(event)

    def on_stop(self) -> None:
        self._reset_opposing_exit()
        super().on_stop()
