"""v29 independent-external-draw certificate over v28 and v27 costs.

A failed-auction reversal is executable only when its target is an independently
pre-existing external liquidity hazard. Targets inferred only from source-range
acceptance or contemporaneous context are diagnostic, not independent draws.
The exact ablation restores v28 without this certificate.
"""
from __future__ import annotations

from dataclasses import replace
from math import isfinite
import os
from typing import Any

import pandas as pd

from c10_v28_overlay import (  # re-export for the patched Candidate 11 runner
    CostAwareRiskSizer,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
)


def repair_kline_flow_frame(frame: Any, filename: str) -> tuple[Any, list[dict[str, Any]]]:
    """Repair only internally impossible vendor kline flow rows.

    Binance occasionally publishes a row where base volume is smaller than
    taker-buy base volume and inconsistent with quote volume. At bar close the
    quote volume and OHLC are known, so total base volume is deterministically
    reconstructed as quote volume divided by OHLC4. Valid rows are untouched.
    """

    result = frame.copy()
    names = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    )
    values = {name: pd.to_numeric(result[name], errors="raise") for name in names}
    tolerance = 0.001
    invalid = (
        (values["taker_buy_volume"] > values["volume"] * (1.0 + 1e-9))
        | (values["taker_buy_quote_volume"] > values["quote_volume"] * (1.0 + 1e-9))
        | (values["quote_volume"] < values["volume"] * values["low"] * (1.0 - tolerance))
        | (values["quote_volume"] > values["volume"] * values["high"] * (1.0 + tolerance))
    )
    repairs: list[dict[str, Any]] = []
    if bool(invalid.any()):
        # Support both Arrow-backed strings from archive parsing and numeric
        # frames used by regression tests without coercing valid values.
        result["volume"] = result["volume"].astype("object")
    for index in result.index[invalid]:
        open_price = float(values["open"].loc[index])
        high = float(values["high"].loc[index])
        low = float(values["low"].loc[index])
        close = float(values["close"].loc[index])
        original_volume = float(values["volume"].loc[index])
        quote_volume = float(values["quote_volume"].loc[index])
        taker_buy_volume = float(values["taker_buy_volume"].loc[index])
        taker_buy_quote = float(values["taker_buy_quote_volume"].loc[index])
        typical_price = (open_price + high + low + close) / 4.0
        derived_volume = quote_volume / typical_price if typical_price > 0.0 else float("nan")
        if (
            not all(isfinite(value) for value in (derived_volume, quote_volume, taker_buy_volume, taker_buy_quote))
            or derived_volume <= 0.0
            or taker_buy_quote > quote_volume * (1.0 + 1e-9)
            or derived_volume < taker_buy_volume * (1.0 - tolerance)
        ):
            raise RuntimeError(
                f"unrepairable kline flow row: {filename}: index={index}",
            )
        result.at[index, "volume"] = format(derived_volume, ".12f")
        repairs.append(
            {
                "filename": filename,
                "open_time": int(result.at[index, "open_time"]),
                "reason": "TOTAL_BASE_VOLUME_INCONSISTENT_WITH_TAKER_AND_QUOTE_VOLUME",
                "original_volume": original_volume,
                "repaired_volume": derived_volume,
                "taker_buy_volume": taker_buy_volume,
                "quote_volume": quote_volume,
                "method": "QUOTE_VOLUME_DIVIDED_BY_COMPLETED_BAR_OHLC4",
            },
        )
    return result, repairs


def certify_plan(plan: Any, decision: Any) -> Any:
    if (
        os.environ.get("C10_V29_ABLATE_EXTERNAL_DRAW", "0") == "1"
        or not decision.approved
        or getattr(getattr(plan, "scenario", None), "value", None) != "FAR"
    ):
        return decision
    method = str(getattr(plan, "details", {}).get("draw_method", ""))
    if method != "EXTERNAL_HAZARD_DOMINANCE":
        return replace(
            decision,
            approved=False,
            reason="FAR_REQUIRES_INDEPENDENT_EXTERNAL_DRAW",
        )
    return replace(
        decision,
        reason=f"INDEPENDENT_DRAW_{decision.reason}",
    )
