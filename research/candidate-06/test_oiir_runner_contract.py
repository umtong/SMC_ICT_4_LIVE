from __future__ import annotations

import inspect

from open_interest_inventory_nautilus_runner import (
    run_open_interest_inventory_nautilus_backtest,
)
from open_interest_inventory_regime_engine import (
    OpenInterestInventoryRegimeRelayEngine,
)


def main() -> None:
    runner = inspect.getsource(
        run_open_interest_inventory_nautilus_backtest,
    )
    observe = inspect.getsource(
        OpenInterestInventoryRegimeRelayEngine.observe,
    )
    assert "BacktestEngine" in runner
    assert "engine.add_venue" in runner
    assert "engine.add_instrument" in runner
    assert "engine.add_data" in runner
    assert "engine.add_strategy" in runner
    assert "OpenInterestInventoryRegimeRelayEngine" in runner
    assert observe.index("_maybe_start_wave") < observe.index(
        "_ingest_metric_history",
    )
    assert 'signal_submission_timing": "ON_SIGNAL_CLOSE"' not in runner
    print("OIIR Nautilus-only and prior-only runner contracts passed")


if __name__ == "__main__":
    main()
