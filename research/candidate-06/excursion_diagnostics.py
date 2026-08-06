"""Post-close path diagnostics derived from causally replayed Nautilus bars."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def _net_r(
    *,
    entry: float,
    exit_price: float,
    direction: str,
    fee_rate: float,
    tick: float,
    loss_per_unit: float,
) -> float:
    gross = exit_price - entry if direction == "LONG" else entry - exit_price
    estimated_cost = entry * fee_rate + exit_price * fee_rate + 2.0 * tick
    return (gross - estimated_cost) / loss_per_unit if loss_per_unit > 0.0 else 0.0


def calculate_excursion_diagnostics(
    trade: Mapping[str, Any],
    observations: Iterable[Any],
    *,
    closed_ts_ns: int,
    tick: float,
) -> dict[str, Any]:
    """Measure MFE/MAE only between the Nautilus open and close events.

    These values are diagnostic outputs written after the position has closed;
    they never enter the trading decision which generated the position.
    """
    opened_ts_ns = int(trade.get("opened_ts_ns", closed_ts_ns))
    entry = float(trade["actual_entry_price"])
    direction = str(trade["direction"])
    stop_distance = abs(entry - float(trade["stop_price"]))
    atr = float(trade.get("atr_at_signal", 0.0))
    fee_rate = float(trade.get("fee_rate_per_fill", 0.0))
    loss_per_unit = float(trade.get("loss_per_unit", stop_distance))
    ordered = sorted(
        (
            observation
            for observation in observations
            if opened_ts_ns <= int(observation.ts_ns) <= int(closed_ts_ns)
        ),
        key=lambda observation: int(observation.ts_ns),
    )
    if not ordered:
        return {
            "path_observations": 0,
            "mfe_price_distance": 0.0,
            "mae_price_distance": 0.0,
            "mfe_stop_units": 0.0,
            "mae_stop_units": 0.0,
            "mfe_close_net_r_after_cost": 0.0,
            "mfe_intrabar_net_r_after_cost": 0.0,
            "first_close_half_r_ts_ns": None,
            "first_close_one_r_ts_ns": None,
        }

    if direction == "LONG":
        favorable_prices = [float(observation.high) for observation in ordered]
        adverse_prices = [float(observation.low) for observation in ordered]
        close_prices = [float(observation.close) for observation in ordered]
        best_price = max(favorable_prices)
        worst_price = min(adverse_prices)
        mfe_distance = max(0.0, best_price - entry)
        mae_distance = max(0.0, entry - worst_price)
    elif direction == "SHORT":
        favorable_prices = [float(observation.low) for observation in ordered]
        adverse_prices = [float(observation.high) for observation in ordered]
        close_prices = [float(observation.close) for observation in ordered]
        best_price = min(favorable_prices)
        worst_price = max(adverse_prices)
        mfe_distance = max(0.0, entry - best_price)
        mae_distance = max(0.0, worst_price - entry)
    else:
        raise ValueError(f"unsupported direction: {direction}")

    close_net_rs = [
        _net_r(
            entry=entry,
            exit_price=close_price,
            direction=direction,
            fee_rate=fee_rate,
            tick=tick,
            loss_per_unit=loss_per_unit,
        )
        for close_price in close_prices
    ]
    intrabar_net_r = _net_r(
        entry=entry,
        exit_price=best_price,
        direction=direction,
        fee_rate=fee_rate,
        tick=tick,
        loss_per_unit=loss_per_unit,
    )

    first_half = None
    first_one = None
    for observation, net_r in zip(ordered, close_net_rs, strict=True):
        if first_half is None and net_r >= 0.5:
            first_half = int(observation.ts_ns)
        if first_one is None and net_r >= 1.0:
            first_one = int(observation.ts_ns)

    return {
        "path_observations": len(ordered),
        "mfe_price": best_price,
        "mae_price": worst_price,
        "mfe_price_distance": mfe_distance,
        "mae_price_distance": mae_distance,
        "mfe_stop_units": mfe_distance / stop_distance if stop_distance > 0.0 else 0.0,
        "mae_stop_units": mae_distance / stop_distance if stop_distance > 0.0 else 0.0,
        "mfe_atr": mfe_distance / atr if atr > 0.0 else 0.0,
        "mae_atr": mae_distance / atr if atr > 0.0 else 0.0,
        "mfe_close_net_r_after_cost": max(close_net_rs),
        "mfe_intrabar_net_r_after_cost": intrabar_net_r,
        "first_close_half_r_ts_ns": first_half,
        "first_close_one_r_ts_ns": first_one,
    }
