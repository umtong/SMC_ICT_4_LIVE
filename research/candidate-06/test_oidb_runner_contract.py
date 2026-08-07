from __future__ import annotations

from pathlib import Path


def main() -> None:
    source = (Path(__file__).resolve().parent / "open_interest_deleveraging_nautilus_runner.py").read_text(encoding="utf-8")
    for token in (
        "BacktestEngine",
        "OpenInterestDeleveragingBifurcationEngine",
        "engine.add_instrument",
        "engine.add_data",
        "engine.add_strategy",
        "OtoTriggerMode.PARTIAL",
    ):
        assert token in source, token
    assert "custom backtest" not in source.lower()
    print("OIDB Nautilus-only runner contract passed")


if __name__ == "__main__":
    main()
