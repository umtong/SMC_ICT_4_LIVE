"""Causal decision features for the restored liquidity episode policy.

Only information available at or before each episode decision timestamp is used.
Outcome, fill, MFE/MAE and resolution fields are never used as model inputs.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

EPS = 1e-12


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _row_number(row: pd.Series, *names: str, default: float = 0.0) -> float:
    for name in names:
        if name in row.index:
            value = _number(row.get(name), float("nan"))
            if math.isfinite(value):
                return value
    return default


def _signed(side: str, value: float) -> float:
    return value if side == "LONG" else -value


def _decision_index(data: pd.DataFrame, order_time_ns: Any) -> int | None:
    value = pd.to_numeric(pd.Series([order_time_ns]), errors="coerce").iloc[0]
    if pd.isna(value) or data.empty:
        return None
    timestamp = pd.Timestamp(int(value), unit="ns", tz="UTC")
    position = int(data.index.searchsorted(timestamp))
    if position >= len(data):
        position = len(data) - 1
    if position > 0 and data.index[position] > timestamp:
        position -= 1
    return max(0, position)


def _decision_context(data: pd.DataFrame, index: int, side: str) -> dict[str, float]:
    row = data.iloc[index]
    direction = 1.0 if side == "LONG" else -1.0

    common_5 = direction * _row_number(row, "common_return_5m", "factor_return_5m")
    common_15 = direction * _row_number(row, "common_return_15m", "factor_return_15m")
    common_60 = direction * _row_number(row, "common_return_60m", "factor_return_60m")
    breadth_5 = direction * _row_number(row, "common_breadth_5m", "common_breadth", "breadth")
    breadth_15 = direction * _row_number(row, "common_breadth_15m")
    breadth_60 = direction * _row_number(row, "common_breadth_60m")
    residual_5 = direction * _row_number(
        row, "residual_return_5m", "relative_return_5m", "residual_return"
    )
    residual_15 = direction * _row_number(row, "residual_return_15m", "relative_return_15m")
    residual_60 = direction * _row_number(row, "residual_return_60m", "relative_return_60m")
    trend_15 = direction * _row_number(row, "structure_15m_trend_state")
    trend_60 = direction * _row_number(row, "structure_60m_trend_state")
    trend_240 = direction * _row_number(row, "structure_240m_trend_state")
    trend_vote = direction * _row_number(row, "structure_multiscale_trend_vote")
    agreement = _row_number(row, "structure_multiscale_trend_agreement")

    momentum_vote = float(
        np.mean(
            [
                np.sign(common_5),
                np.sign(common_15),
                np.sign(common_60),
                np.sign(residual_5),
                np.sign(residual_15),
            ]
        )
    )
    breadth_vote = float(np.mean([breadth_5, breadth_15, breadth_60]))
    structure_vote = float(np.mean([trend_15, trend_60, trend_240, trend_vote]))

    return {
        "ctx_common_return_5m_signed": common_5,
        "ctx_common_return_15m_signed": common_15,
        "ctx_common_return_60m_signed": common_60,
        "ctx_common_breadth_5m_signed": breadth_5,
        "ctx_common_breadth_15m_signed": breadth_15,
        "ctx_common_breadth_60m_signed": breadth_60,
        "ctx_residual_return_5m_signed": residual_5,
        "ctx_residual_return_15m_signed": residual_15,
        "ctx_residual_return_60m_signed": residual_60,
        "ctx_structure_15m_signed": trend_15,
        "ctx_structure_60m_signed": trend_60,
        "ctx_structure_240m_signed": trend_240,
        "ctx_structure_vote_signed": trend_vote,
        "ctx_structure_agreement": agreement,
        "ctx_momentum_vote": momentum_vote,
        "ctx_breadth_vote": breadth_vote,
        "ctx_structure_vote": structure_vote,
        "ctx_oi_log_change": _row_number(row, "metric_oi_log_change_1", "oi_log_change_1"),
        "ctx_basis_change_bps_signed": direction
        * _row_number(row, "basis_change_3m_bps", "basis_change_bps"),
        "ctx_dealing_range_position_signed": direction
        * (2.0 * _row_number(row, "dealing_range_position", default=0.5) - 1.0),
    }


def enrich_episode_frame(frame: pd.DataFrame, data: pd.DataFrame) -> pd.DataFrame:
    """Append causal context and a transparent mechanism-coherence prior."""
    if frame.empty:
        return frame.copy()
    output = frame.copy()
    contexts: list[dict[str, float]] = []
    for _, row in output.iterrows():
        index = _decision_index(data, row.get("order_time_ns"))
        if index is None:
            contexts.append({})
            continue
        contexts.append(_decision_context(data, index, str(row.get("side", ""))))
    context_frame = pd.DataFrame(contexts, index=output.index)
    output = pd.concat([output, context_frame], axis=1)

    for family in (
        "FAILED_AUCTION_REVERSAL",
        "ACCEPTED_AUCTION_CONTINUATION",
        "INITIATIVE_MITIGATION_CONTINUATION",
    ):
        output[f"family_{family.lower()}"] = (
            output.get("family", pd.Series("", index=output.index)).astype(str).eq(family).astype(float)
        )

    for geometry in (
        "OB_FVG_OVERLAP",
        "BULLISH_FVG",
        "BEARISH_FVG",
        "LAST_OPPOSITE_BODY",
        "TRANSFERRED_SOURCE",
        "CAUSAL_DEPARTURE_BAND",
    ):
        output[f"geometry_{geometry.lower()}"] = (
            output.get("entry_geometry", pd.Series("", index=output.index))
            .astype(str)
            .str.contains(geometry, regex=False)
            .astype(float)
        )

    source_scale = pd.to_numeric(output.get("source_scale", 0.0), errors="coerce").fillna(0.0)
    route_scale = pd.to_numeric(output.get("route_scale", 0.0), errors="coerce").fillna(0.0)
    output["source_scale_log"] = np.log1p(np.maximum(source_scale, 0.0))
    output["route_scale_log"] = np.log1p(np.maximum(route_scale, 0.0))
    output["route_to_source_log_ratio"] = np.log(
        np.maximum(route_scale, 1.0) / np.maximum(source_scale, 1.0)
    )

    control_move = pd.to_numeric(output.get("control_move_atr", 0.0), errors="coerce").fillna(0.0)
    control_efficiency = pd.to_numeric(
        output.get("control_path_efficiency", 0.0), errors="coerce"
    ).fillna(0.0)
    control_flow = pd.to_numeric(
        output.get("control_flow_share_signed", 0.0), errors="coerce"
    ).fillna(0.0)
    control_activity = pd.to_numeric(
        output.get("control_activity_ratio", 0.0), errors="coerce"
    ).fillna(0.0)
    control_effort = pd.to_numeric(
        output.get("control_effort_result", 0.0), errors="coerce"
    ).fillna(0.0)

    output["control_composite"] = (
        0.32 * np.tanh(control_move)
        + 0.26 * np.clip(control_efficiency, -1.0, 1.0)
        + 0.18 * np.tanh(3.0 * control_flow)
        + 0.12 * np.tanh(np.log1p(np.maximum(control_activity, 0.0)))
        + 0.12 * np.tanh(control_effort)
    )

    market_alignment = (
        0.35 * pd.to_numeric(output.get("ctx_momentum_vote", 0.0), errors="coerce").fillna(0.0)
        + 0.35 * pd.to_numeric(output.get("ctx_breadth_vote", 0.0), errors="coerce").fillna(0.0)
        + 0.30 * pd.to_numeric(output.get("ctx_structure_vote", 0.0), errors="coerce").fillna(0.0)
    )
    output["market_alignment"] = market_alignment

    failed = output.get("family", pd.Series("", index=output.index)).astype(str).eq(
        "FAILED_AUCTION_REVERSAL"
    )
    accepted = output.get("family", pd.Series("", index=output.index)).astype(str).eq(
        "ACCEPTED_AUCTION_CONTINUATION"
    )
    mitigation = output.get("family", pd.Series("", index=output.index)).astype(str).eq(
        "INITIATIVE_MITIGATION_CONTINUATION"
    )
    coherence = 0.58 * output["control_composite"] + 0.42 * market_alignment
    coherence = np.where(failed, 0.72 * output["control_composite"] + 0.28 * market_alignment, coherence)
    coherence = np.where(accepted, 0.52 * output["control_composite"] + 0.48 * market_alignment, coherence)
    coherence = np.where(mitigation, 0.48 * output["control_composite"] + 0.52 * market_alignment, coherence)
    output["mechanism_coherence"] = coherence
    output["episode_policy_version"] = "liquidity-episode-policy-v1"
    return output


FEATURE_COLUMNS = [
    "family_failed_auction_reversal",
    "family_accepted_auction_continuation",
    "family_initiative_mitigation_continuation",
    "geometry_ob_fvg_overlap",
    "geometry_bullish_fvg",
    "geometry_bearish_fvg",
    "geometry_last_opposite_body",
    "geometry_transferred_source",
    "geometry_causal_departure_band",
    "control_move_atr",
    "control_path_efficiency",
    "control_flow_share_signed",
    "control_activity_ratio",
    "control_effort_result",
    "common_factor_signed",
    "common_breadth_signed",
    "relative_return_signed",
    "oi_log_change",
    "basis_change_signed_bps",
    "ctx_common_return_5m_signed",
    "ctx_common_return_15m_signed",
    "ctx_common_return_60m_signed",
    "ctx_common_breadth_5m_signed",
    "ctx_common_breadth_15m_signed",
    "ctx_common_breadth_60m_signed",
    "ctx_residual_return_5m_signed",
    "ctx_residual_return_15m_signed",
    "ctx_residual_return_60m_signed",
    "ctx_structure_15m_signed",
    "ctx_structure_60m_signed",
    "ctx_structure_240m_signed",
    "ctx_structure_vote_signed",
    "ctx_structure_agreement",
    "ctx_momentum_vote",
    "ctx_breadth_vote",
    "ctx_structure_vote",
    "ctx_oi_log_change",
    "ctx_basis_change_bps_signed",
    "ctx_dealing_range_position_signed",
    "source_scale_log",
    "route_scale_log",
    "route_to_source_log_ratio",
    "source_strength",
    "source_confluence_count",
    "route_strength",
    "gross_rr",
    "risk_bps",
    "planned_target_net_r",
    "decision_quality",
    "control_composite",
    "market_alignment",
    "mechanism_coherence",
]
