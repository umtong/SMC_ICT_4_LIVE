"""Period-blocked meta-labeling experiment for the V15 loss engine."""
from __future__ import annotations

import copy
from datetime import date
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-55"
REUSED = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-55-v15-meta"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
CACHE = ROOT / ".cache" / "candidate-55-v15-meta"
DEVELOPMENT = {
    "dev-2026-03": ("2026-03-01", "2026-03-07"),
    "dev-2025-02": ("2025-02-10", "2025-02-16"),
    "dev-2025-09": ("2025-09-01", "2025-09-14"),
}
CONFIRMATION = ("2025-04-01", "2025-04-14")
MEDIUM = ("2024-10-01", "2024-10-30")
LONG = ("2024-03-01", "2024-05-29")
FEATURES = (
    "relative_aligned",
    "premium_change_5m",
    "efficiency_60s",
    "absorption_60s",
    "aligned_flow_3m",
    "aligned_ret_60s_bps",
    "log_notional_burst",
    "log_trade_count_burst",
    "oi_change_15m",
    "source_score",
    "breakout_bps",
)

_HELPER_SPEC = importlib.util.spec_from_file_location(
    "candidate55_v15_meta_helpers", CANDIDATE / "run_zaratustra_v15_repair.py"
)
if _HELPER_SPEC is None or _HELPER_SPEC.loader is None:
    raise RuntimeError("cannot load shared result helpers")
_HELPER = importlib.util.module_from_spec(_HELPER_SPEC)
sys.modules[_HELPER_SPEC.name] = _HELPER
_HELPER_SPEC.loader.exec_module(_HELPER)


def root(stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-v15-meta-{stage}"


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def days(interval: tuple[str, str]) -> int:
    return (date.fromisoformat(interval[1]) - date.fromisoformat(interval[0])).days + 1


def base_config() -> dict[str, Any]:
    config = copy.deepcopy(json.loads((REUSED / "config.json").read_text(encoding="utf-8")))
    for key in ("sma_offset_low", "sma_offset_high", "sma_stop_min_fraction", "sma_stop_max_fraction", "sma_stop_atr_buffer"):
        config["strategy"].pop(key, None)
    config["strategy"].update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 1_000_000,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "zaratustra_variant": "edge_exact",
            "zaratustra_startup_30m_candles": 10,
            "zaratustra_rsi_period": 14,
            "zaratustra_di_period": 14,
            "zaratustra_bb_period": 20,
            "zaratustra_source_leverage": 10.0,
            "zaratustra_source_stoploss": 0.15,
            "zaratustra_trailing_positive": 0.012,
            "zaratustra_trailing_offset": 0.107,
            "zaratustra_emergency_target_fraction": 0.50,
            "v15_relative_mode": "bb_relative_short",
            "v15_relative_lookback_minutes": 60,
            "v15_relative_min_fraction": 0.001,
            "v15_relative_max_fraction": 0.004,
            "v15_short_min_premium_change_5m": -0.00005,
            "v15_long_max_premium_change_5m": 0.00005,
            "v15_accepted_mode": "relative_basis_short",
            "v15_acceptance_efficiency_min": 0.007,
            "v15_acceptance_absorption_max": 0.37,
        }
    )
    return config


def launch(config: Path, stage: str, interval: tuple[str, str]) -> int:
    command = [
        sys.executable, str(REUSED / "launch.py"), "--config", str(config),
        "--start", interval[0], "--end", interval[1], "--cache", str(CACHE),
        "--output", str(root(stage)), "--workspace", str(WORK / stage),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REUSED) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    print("RUN", stage, interval, flush=True)
    return int(subprocess.run(command, env=env, check=False).returncode)


def pnl(value: Any) -> float:
    match = re.search(r"[-+]?\d[\d_,]*(?:\.\d+)?", str(value))
    return float(match.group(0).replace("_", "").replace(",", "")) if match else 0.0


def feature_row(frame: pd.DataFrame, ts: int) -> pd.Series | None:
    times = frame["observed_time_ns"].to_numpy(dtype=np.int64)
    index = int(np.searchsorted(times, int(ts), side="right") - 1)
    return None if index < 0 else frame.iloc[index]


def training_table(stage: str) -> pd.DataFrame:
    destination = root(stage)
    scenarios = json.loads((destination / "closed_scenarios.json").read_text(encoding="utf-8"))
    stores = {
        symbol: pd.read_csv(destination / "source" / symbol / "features.csv.gz", compression="gzip")
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    }
    rows = []
    for scenario in scenarios:
        symbol = str(scenario["symbol"])
        observed = feature_row(stores[symbol], int(scenario["episode_ts"]))
        if observed is None:
            continue
        diagnostics = scenario.get("diagnostics", {})
        risk = float(scenario.get("risk_budget") or 0.0)
        if risk <= 0.0:
            continue
        values = {
            "relative_aligned": float(diagnostics.get("side_aligned_relative_fraction", math.nan)),
            "premium_change_5m": float(observed["premium_change_5m"]),
            "efficiency_60s": float(observed["efficiency_60s"]),
            "absorption_60s": float(observed["absorption_60s"]),
            "aligned_flow_3m": -float(observed["flow_3m"]),
            "aligned_ret_60s_bps": -float(observed["ret_60s_bps"]),
            "log_notional_burst": math.log1p(max(0.0, float(observed["notional_burst"]))),
            "log_trade_count_burst": math.log1p(max(0.0, float(observed["trade_count_burst"]))),
            "oi_change_15m": float(observed["oi_change_15m"]),
            "source_score": float(scenario.get("score") or 0.0),
            "breakout_bps": float(diagnostics.get("breakout_bps", 0.0)),
            "stage": stage,
            "pnl": pnl(scenario.get("realized_pnl")),
            "risk_budget": risk,
        }
        if all(math.isfinite(values[name]) for name in FEATURES):
            values["realized_r"] = float(np.clip(values["pnl"] / risk, -1.5, 3.0))
            rows.append(values)
    return pd.DataFrame(rows)


def fit_model(frame: pd.DataFrame, ridge: float = 2.0):
    x = frame.loc[:, FEATURES].to_numpy(dtype=float)
    y = frame["realized_r"].to_numpy(dtype=float)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales < 1e-9] = 1.0
    z = (x - means) / scales
    intercept = float(y.mean())
    coefficients = np.linalg.solve(z.T @ z + ridge * np.eye(z.shape[1]), z.T @ (y - intercept))
    return means, scales, coefficients, intercept


def predict(frame, model):
    means, scales, coefficients, intercept = model
    z = (frame.loc[:, FEATURES].to_numpy(dtype=float) - means) / scales
    return intercept + z @ coefficients


def choose_threshold(frame: pd.DataFrame) -> tuple[float, dict[str, Any]]:
    oof = np.full(len(frame), np.nan)
    for stage in sorted(frame["stage"].unique()):
        train = frame[frame["stage"] != stage]
        test_index = frame.index[frame["stage"] == stage]
        if len(train) < len(FEATURES) + 2:
            continue
        oof[test_index] = predict(frame.loc[test_index], fit_model(train))
    clean = np.isfinite(oof)
    candidates = sorted(set(np.quantile(oof[clean], np.linspace(0.10, 0.80, 15)).tolist()))
    best = None
    for threshold in candidates:
        selected = frame[clean & (oof >= threshold)]
        if selected.empty:
            continue
        stage_net = selected.groupby("stage")["pnl"].sum().to_dict()
        positive = sum(value > 0.0 for value in stage_net.values())
        gp = selected.loc[selected.pnl > 0.0, "pnl"].sum()
        gl = -selected.loc[selected.pnl < 0.0, "pnl"].sum()
        score = (positive, min(stage_net.values(), default=-math.inf), gp - gl, len(selected))
        record = {
            "threshold": float(threshold), "trades": int(len(selected)), "positive_periods": int(positive),
            "period_net": {str(k): float(v) for k, v in stage_net.items()}, "gross_profit": float(gp),
            "gross_loss": float(gl), "net_pnl": float(gp - gl), "profit_factor": float(gp / gl) if gl > 0 else None,
        }
        if best is None or score > best[0]:
            best = (score, record)
    if best is None:
        raise RuntimeError("no V15 meta threshold candidate")
    return float(best[1]["threshold"]), best[1]


def result(stage: str, interval: tuple[str, str], code: int) -> dict[str, Any]:
    _HELPER.output_root = lambda _variant, _stage: root(stage)
    return _HELPER.read_result("meta", stage, interval, code)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CANDIDATE / "strategy_zaratustra_v15_accepted.py", REUSED / "strategy.py")
    config = base_config()
    base_path = WORK / "config-base.json"
    dump(base_path, config)
    dev_codes = {stage: launch(base_path, stage, interval) for stage, interval in DEVELOPMENT.items()}
    tables = [training_table(stage) for stage in DEVELOPMENT]
    frame = pd.concat(tables, ignore_index=True)
    if len(frame) < 25:
        raise RuntimeError(f"insufficient V15 meta development trades: {len(frame)}")
    threshold, threshold_evidence = choose_threshold(frame)
    model = fit_model(frame)
    means, scales, coefficients, intercept = model

    shutil.copyfile(CANDIDATE / "strategy_zaratustra_v15_meta.py", REUSED / "strategy.py")
    meta_config = base_config()
    meta_config["strategy"].update(
        {
            "v15_meta_feature_names_json": json.dumps(list(FEATURES)),
            "v15_meta_means_json": json.dumps(means.tolist()),
            "v15_meta_scales_json": json.dumps(scales.tolist()),
            "v15_meta_coefficients_json": json.dumps(coefficients.tolist()),
            "v15_meta_intercept": float(intercept),
            "v15_meta_threshold": float(threshold),
        }
    )
    meta_path = WORK / "config-meta.json"
    dump(meta_path, meta_config)
    confirm_code = launch(meta_path, "confirm-2025-04", CONFIRMATION)
    confirm = result("confirm-2025-04", CONFIRMATION, confirm_code)
    confirm_strong = bool(
        confirm.get("produced")
        and float(confirm.get("ending_nav") or 0.0) > float(confirm.get("starting_nav") or 0.0)
        and float(confirm.get("profit_factor") or 0.0) >= 1.25
        and float(confirm.get("trades_per_day") or 0.0) >= 0.6
        and float(confirm.get("gross_profit_per_day") or 0.0) >= 600.0
        and bool(confirm.get("mechanically_valid"))
    )
    medium = {"produced": False, "not_run_reason": "confirmation not structurally strong"}
    if confirm_strong:
        code = launch(meta_path, "medium-2024-10", MEDIUM)
        medium = result("medium-2024-10", MEDIUM, code)
    medium_strong = bool(
        medium.get("produced")
        and float(medium.get("geometric_daily_growth") or 0.0) >= 0.005
        and float(medium.get("profit_factor") or 0.0) >= 1.25
        and int(medium.get("trades") or 0) >= days(MEDIUM)
        and float(medium.get("gross_profit_per_day") or 0.0) >= 800.0
        and float(medium.get("max_drawdown") or 1.0) <= 0.20
        and bool(medium.get("mechanically_valid"))
    )
    long_result = {"produced": False, "not_run_reason": "medium not strong enough"}
    if medium_strong:
        code = launch(meta_path, "long-2024-03_05", LONG)
        long_result = result("long-2024-03_05", LONG, code)

    final = {
        "candidate": "candidate-55", "method": "PERIOD_BLOCKED_RIDGE_META_LABELING",
        "primary_policy_relearned": False, "feature_names": list(FEATURES),
        "development_periods": DEVELOPMENT, "development_trades": int(len(frame)),
        "development_gross_profit": float(frame.loc[frame.pnl > 0, "pnl"].sum()),
        "development_gross_loss": float(-frame.loc[frame.pnl < 0, "pnl"].sum()),
        "oof_threshold_evidence": threshold_evidence,
        "frozen_model": {
            "means": means.tolist(), "scales": scales.tolist(), "coefficients": coefficients.tolist(),
            "intercept": float(intercept), "threshold": float(threshold),
        },
        "confirmation": confirm, "confirmation_strong": confirm_strong,
        "medium": medium, "medium_strong": medium_strong, "long": long_result,
        "production_ready": bool(
            long_result.get("produced")
            and float(long_result.get("geometric_daily_growth") or 0.0) >= 0.01
            and int(long_result.get("trades") or 0) >= days(LONG)
            and float(long_result.get("expectancy_usdt") or 0.0) > 0.0
            and float(long_result.get("max_drawdown") or 1.0) <= 0.20
        ),
    }
    dump(ARTIFACTS / "zaratustra-v15-meta-final-result.json", final)
    dump(CANDIDATE / "evidence" / "v15-meta" / "FROZEN_MODEL.json", final)
    print(json.dumps(final, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
