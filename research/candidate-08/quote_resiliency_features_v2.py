"""Contract-correct feature join for quote resiliency V1.

V2 retains the quote-event and completed-bucket definitions from V1, while correcting the joined
schema check and keeping response-ratio regularization in base-quantity units.  Production streaming
never combines medians from independently split buckets; the data loader carries the final open
bucket into the next chunk before calling ``aggregate_quote_events``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import quote_resiliency_features as base


IMPLEMENTATION_REVISION = "CAUSAL_QUOTE_RESILIENCY_FEATURES_V2_SCHEMA_SAFE"
QUOTE_FLOW_COLUMNS = base.QUOTE_FLOW_COLUMNS
QUOTE_STATE_COLUMNS = base.QUOTE_STATE_COLUMNS
TRADE_COLUMNS = base.TRADE_COLUMNS
QuoteResiliencyConfig = base.QuoteResiliencyConfig
aggregate_quote_events = base.aggregate_quote_events
quote_event_rows = base.quote_event_rows
validate_exact_cadence = base.validate_exact_cadence


def build_quote_resiliency_features(
    *,
    trade_bars: pd.DataFrame,
    quote_buckets: pd.DataFrame,
    tick: float,
    config: QuoteResiliencyConfig | None = None,
) -> pd.DataFrame:
    """Join exact completed streams and compute only shifted, causal normalizations."""

    cfg = config or QuoteResiliencyConfig()
    cfg.validate()
    if tick <= 0.0:
        raise ValueError("tick must be positive")
    base._require_time_index(trade_bars, name="trade_bars")
    base._require_time_index(quote_buckets, name="quote_buckets")
    validate_exact_cadence(trade_bars.index, seconds=cfg.cadence_seconds)
    missing_trade = sorted(set(TRADE_COLUMNS) - set(trade_bars.columns))
    if missing_trade:
        raise ValueError(f"trade_bars missing columns: {missing_trade}")

    state_columns = [
        "bid_open",
        "bid_close",
        "bid_qty_open",
        "bid_qty_close",
        "ask_open",
        "ask_close",
        "ask_qty_open",
        "ask_qty_close",
        "mid_open",
        "mid_high",
        "mid_low",
        "mid_close",
        "microprice_close",
        "quote_imbalance_close",
        "spread_open",
        "spread_max",
        "spread_median",
        "spread_close",
    ]
    count_columns = [
        "quote_update_count",
        "quote_price_change_count",
        "quote_size_only_change_count",
    ]
    required_quote = set(state_columns) | set(QUOTE_FLOW_COLUMNS) | set(count_columns)
    missing_quote = sorted(required_quote - set(quote_buckets.columns))
    if missing_quote:
        raise ValueError(f"quote_buckets missing columns: {missing_quote}")

    quote = quote_buckets.reindex(trade_bars.index).copy()
    quote[state_columns] = quote[state_columns].ffill()
    flow_columns = list(QUOTE_FLOW_COLUMNS) + count_columns
    quote[flow_columns] = quote[flow_columns].fillna(0.0)

    result = trade_bars.loc[:, list(TRADE_COLUMNS)].copy().join(quote)
    result["imbalance"] = result["signed_volume"] / result["volume"].replace(0.0, np.nan)
    result["buy_volume"] = 0.5 * (result["volume"] + result["signed_volume"])
    result["sell_volume"] = 0.5 * (result["volume"] - result["signed_volume"])
    result["aggressive_flow_scale"] = base._rolling_quantile(
        result["signed_volume"].abs(),
        bars=cfg.baseline_bars,
        minimum=cfg.minimum_history_bars,
        quantile=cfg.pressure_quantile,
    )
    result["quote_ofi_scale"] = base._rolling_quantile(
        result["quote_ofi_qty"].abs(),
        bars=cfg.baseline_bars,
        minimum=cfg.minimum_history_bars,
        quantile=cfg.quote_ofi_quantile,
    )
    result["spread_causal_median"] = result["spread_close"].shift(1).rolling(
        cfg.baseline_bars,
        min_periods=cfg.minimum_history_bars,
    ).median()
    result["aggressive_pressure_ratio"] = (
        result["signed_volume"] / result["aggressive_flow_scale"].replace(0.0, np.nan)
    )
    result["quote_ofi_ratio"] = (
        result["quote_ofi_qty"] / result["quote_ofi_scale"].replace(0.0, np.nan)
    )
    result["spread_median_ratio"] = (
        result["spread_close"] / result["spread_causal_median"].replace(0.0, np.nan)
    )
    result["mid_progress_ticks"] = (result["mid_close"] - result["mid_open"]) / tick
    result["mid_excursion_up_ticks"] = (result["mid_high"] - result["mid_open"]) / tick
    result["mid_excursion_down_ticks"] = (result["mid_open"] - result["mid_low"]) / tick
    result["microprice_skew_ticks"] = (
        result["microprice_close"] - result["mid_close"]
    ) / tick
    epsilon_qty = 1e-12
    result["bid_response_ratio"] = (
        result["bid_add_qty"] + epsilon_qty
    ) / (result["bid_remove_qty"] + epsilon_qty)
    result["ask_response_ratio"] = (
        result["ask_add_qty"] + epsilon_qty
    ) / (result["ask_remove_qty"] + epsilon_qty)
    result["quote_updates_per_second"] = (
        result["quote_update_count"] / float(cfg.cadence_seconds)
    )

    required_observable = list(TRADE_COLUMNS) + state_columns + [
        "aggressive_pressure_ratio",
        "quote_ofi_ratio",
        "spread_median_ratio",
    ]
    numeric = result[required_observable].apply(pd.to_numeric, errors="coerce")
    result["quote_resiliency_observable"] = np.isfinite(
        numeric.to_numpy(dtype=np.float64)
    ).all(axis=1)
    result.attrs["implementation_revision"] = IMPLEMENTATION_REVISION
    result.attrs["cadence_seconds"] = cfg.cadence_seconds
    result.attrs["split_bucket_contract"] = "PRODUCTION_LOADER_CARRIES_RAW_OPEN_BUCKET"
    return result


__all__ = [
    "IMPLEMENTATION_REVISION",
    "QUOTE_FLOW_COLUMNS",
    "QUOTE_STATE_COLUMNS",
    "QuoteResiliencyConfig",
    "aggregate_quote_events",
    "build_quote_resiliency_features",
    "quote_event_rows",
    "validate_exact_cadence",
]
