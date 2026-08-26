from __future__ import annotations

from pathlib import Path
import importlib.util

import numpy as np
import pandas as pd

MODULE_PATH = Path(__file__).with_name("reachable_control_router.py")
spec = importlib.util.spec_from_file_location("reachable_control_router", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def _ns(day: int) -> int:
    return int(
        pd.Timestamp("2025-01-01", tz="UTC").value
        + day * 86_400_000_000_000
    )


def _orders() -> pd.DataFrame:
    rows = []
    sequence = 0
    for period, role, start_day in (
        ("dev-2025-a", "dev", 0),
        ("dev-2025-b", "dev", 30),
        ("fresh-2025-c", "fresh", 60),
    ):
        for i in range(80):
            timestamp = _ns(start_day + i // 4)
            good = i % 4 in (0, 1, 2)
            filled = i % 5 != 0
            outcome = "TARGET_FIRST" if good else "STOP_FIRST"
            rows.append(
                {
                    "period": period,
                    "role": role,
                    "episode_id": f"ep-{sequence}",
                    "symbol": ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")[i % 4],
                    "family": (
                        "INITIATIVE_MITIGATION_CONTINUATION"
                        if good
                        else "FAILED_AUCTION_REVERSAL"
                    ),
                    "entry_geometry": (
                        "FIRST_RETEST|OB_FVG_OVERLAP"
                        if good
                        else "CAUSAL_DEPARTURE_BAND"
                    ),
                    "state": (
                        "FIRST_RETEST_ACCEPTED_RESPONSE" if good else "DEPARTURE"
                    ),
                    "order_time_ns": timestamp,
                    "fill_time_ns": (
                        timestamp + 60_000_000_000 if filled else np.nan
                    ),
                    "order_terminal_time_ns": timestamp + 120_000_000_000,
                    "resolution_time_ns": timestamp + 600_000_000_000,
                    "outcome": outcome if filled else "UNFILLED",
                    "gross_rr": 1.35 if good else 6.0,
                    "planned_target_net_r": 1.25 if good else 5.7,
                    "net_r": (1.25 if good else -1.0) if filled else np.nan,
                    "mechanism_coherence": 0.8 if good else -0.5,
                    "control_composite": 0.75 if good else -0.4,
                    "market_alignment": 0.6 if good else -0.4,
                    "control_move_atr": 1.2 if good else 0.2,
                    "control_path_efficiency": 0.8 if good else 0.1,
                    "control_flow_share_signed": 0.4 if good else -0.2,
                    "control_activity_ratio": 2.0 if good else 0.3,
                    "control_effort_result": 0.7 if good else -0.3,
                    "order_exists": True,
                }
            )
            sequence += 1
    return pd.DataFrame(rows)


def test_feature_space_is_shared_and_event_relative() -> None:
    orders = _orders()
    features = module.engineer_features(orders)
    assert "symbol" not in features
    assert "entry" not in features
    assert (
        features.loc[0, "reachable_frontier_prior"]
        > features.loc[3, "reachable_frontier_prior"]
    )


def test_strict_causal_training_uses_only_mature_development_labels() -> None:
    scored, diagnostics = module.strict_causal_score(_orders())
    assert diagnostics["dev-2025-a"]["models_ready"] is False
    assert diagnostics["dev-2025-b"]["models_ready"] is True
    assert diagnostics["fresh-2025-c"]["models_ready"] is True
    assert not scored.loc[scored.period.eq("dev-2025-a"), "policy_eligible"].any()
    fresh = scored[scored.period.eq("fresh-2025-c")]
    assert fresh.causal_models_ready.all()
    assert (
        fresh.loc[fresh.gross_rr.eq(1.35), "reachable_frontier_prior"].mean()
        > fresh.loc[fresh.gross_rr.eq(6.0), "reachable_frontier_prior"].mean()
    )


def test_three_percent_quantity_contract() -> None:
    result = module.risk_sized_quantity(
        nav=10_000.0,
        entry=100.0,
        stop=99.5,
        quantity_step=0.001,
    )
    assert abs(result["risk_fraction"] - 0.03) < 1e-8
    assert abs(result["implied_leverage"] - 6.0) < 1e-8
