"""Report-safe wrapper for the v12 partial-management execution smoke."""
from __future__ import annotations

from decimal import Decimal
import json

from nautilus_trader.model.data import BarType

from backtest_support import make_engine
from instruments import make_instrument
from partial_management_smoke_v12 import HalfThenBreakevenSmoke, _bars


def verify() -> dict[str, object]:
    engine = make_engine()
    instrument = make_instrument("BTCUSDT")
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    engine.add_instrument(instrument)
    engine.add_data(_bars(instrument, bar_type))
    strategy = HalfThenBreakevenSmoke(instrument.id, bar_type)
    engine.add_strategy(strategy)
    try:
        engine.run()
        if not engine.portfolio.is_flat(instrument.id):
            raise RuntimeError("smoke account did not finish flat")
        if strategy.position_closed_count != 1:
            raise RuntimeError(
                f"expected one closed position, got {strategy.position_closed_count}",
            )
        fill_events = [event for event in strategy.events if event["kind"] == "order_filled"]
        filled_quantities = sorted(Decimal(event["last_qty"]) for event in fill_events)
        expected = [Decimal("0.500"), Decimal("0.500"), Decimal("1.000")]
        if filled_quantities != expected:
            raise RuntimeError(f"unexpected fill quantities: {filled_quantities}")
        required_kinds = {
            "first_target_resize_requested",
            "breakeven_stop_submitted",
            "position_closed",
        }
        observed_kinds = {event["kind"] for event in strategy.events}
        missing = sorted(required_kinds - observed_kinds)
        if missing:
            raise RuntimeError(f"partial-management lifecycle missing events: {missing}")
        positions = engine.trader.generate_positions_report()
        if positions.empty:
            raise RuntimeError("position report is empty")
        return {
            "fill_events": len(fill_events),
            "closed_positions": int(len(positions.index)),
            "filled_quantities": [str(value) for value in filled_quantities],
            "events": strategy.events,
            "realized_pnl": str(positions.iloc[-1].get("realized_pnl")),
        }
    finally:
        engine.dispose()


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2, sort_keys=True))
