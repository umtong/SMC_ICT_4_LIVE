"""Run exact-bug versus intended-recursive UTBot 1h interpretations."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path.cwd()
REUSED = ROOT / "research" / "candidate-51"
CANDIDATE = ROOT / "research" / "candidate-55"
WORK = ROOT / ".work" / "candidate-55-utbot-1h-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
EVIDENCE = CANDIDATE / "evidence" / "v8-utbot-1h"
CACHE = ROOT / ".cache" / "candidate-55-utbot-1h-v1"
VARIANTS = ("exact_both", "exact_short", "recursive_both", "recursive_short")
DEVELOPMENT = ("2026-07-15", "2026-07-28")
HOLDOUT = ("2026-06-15", "2026-06-28")
CONTINUOUS = ("2026-05-01", "2026-05-30")
KEEP = ("metrics.json", "strategy_diagnostics.json", "run.json", "data_manifest.json", "closed_scenarios.json")


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def configs() -> dict[str, Path]:
    source = json.loads((REUSED / "config.json").read_text())
    WORK.mkdir(parents=True, exist_ok=True)
    dump(WORK / "manifest.json", {
        "candidate": "candidate-55",
        "family": "public_UTBotAlert_OnlyUT_1h",
        "source_page": "https://freqle.org/strategy/bf7dfb7eda706062",
        "source_interpretations": {
            "exact": "actual zero-array vectorized xATRTrailingStop semantics",
            "recursive": "same clauses evaluated sequentially as intended UTBot",
        },
        "source_parameters": {
            "timeframe": "1h", "leverage": 2.0, "atr_period": 8,
            "key": 2.0, "ema_long": 63, "ema_short": 53,
            "adx_long": [14, 48], "adx_short": [8, 50],
            "volume_long": 40, "volume_short": 37,
            "exit_ema_long": 112, "exit_ema_short": 116,
            "exit_volume_long": 16, "exit_volume_short": 25,
            "stoploss_profit_ratio": -0.298,
            "trailing_positive": 0.012, "trailing_offset": 0.020,
            "roi": {"0": 0.133, "307": 0.099, "781": 0.053, "1856": 0.0},
        },
        "public_result_is_discovery_only": {
            "pairs": 33, "max_open_trades": 10, "trades": 7739,
            "win_rate": 0.790, "profit_factor": 1.72,
            "total_profit_fraction": 7.8868, "max_drawdown_fraction": 0.0803,
            "positive_months": "46/61",
        },
        "project_contract": {
            "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "global_slot": 1, "risk_fraction": 0.03,
            "engine": "NautilusTrader BacktestNode", "real_execution_detail": "1m",
        },
        "development": list(DEVELOPMENT), "holdout": list(HOLDOUT),
        "continuous": list(CONTINUOUS), "variants": list(VARIANTS),
    })
    output: dict[str, Path] = {}
    for name in VARIANTS:
        cfg = copy.deepcopy(source)
        for key in ("sma_offset_low", "sma_offset_high", "sma_stop_min_fraction", "sma_stop_max_fraction", "sma_stop_atr_buffer"):
            cfg["strategy"].pop(key, None)
        cfg["strategy"].update({
            "cooldown_minutes": 0, "max_hold_minutes": 1000000,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "mbe_bucket_minutes": 60, "mbe_variant": name,
            "mbe_rsi_period": 14, "mbe_tema_period": 53, "mbe_bb_period": 63,
            "mbe_source_effective_leverage": 2.0,
            "mbe_source_stoploss": 0.298,
            "mbe_trailing_positive": 0.012,
            "mbe_trailing_offset": 0.020,
            "mbe_emergency_target_fraction": 0.30,
            "mbe_roi_0": 0.133, "mbe_roi_15": 0.099,
            "mbe_roi_41": 0.053, "mbe_roi_114": 0.0,
            "mbe_roi_180": 0.0, "mbe_roi_420": 0.0,
        })
        path = WORK / f"{name}.json"
        dump(path, cfg)
        output[name] = path
    return output


def root(name: str, stage: str) -> Path:
    return ARTIFACTS / f"utbot-1h-v1-{name}-{stage}"


def run(name: str, cfg: Path, stage: str, interval: tuple[str, str]) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REUSED) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = [sys.executable, str(REUSED / "launch.py"), "--config", str(cfg),
           "--start", interval[0], "--end", interval[1], "--cache", str(CACHE),
           "--output", str(root(name, stage)), "--workspace", str(WORK / f"{name}-{stage}")]
    print("RUN", name, stage, interval, flush=True)
    return int(subprocess.run(cmd, env=env, check=False).returncode)


def read(name: str, stage: str, code: int | None = None) -> dict[str, Any]:
    out = root(name, stage)
    mp, dp = out / "metrics.json", out / "strategy_diagnostics.json"
    if not mp.is_file() or not dp.is_file():
        return {"produced": False, "returncode": code}
    m, d = json.loads(mp.read_text()), json.loads(dp.read_text())
    row = {"produced": True, "returncode": code}
    for key in ("ending_nav", "total_return", "geometric_daily_growth", "max_drawdown", "trades", "wins", "losses", "win_rate", "profit_factor", "expectancy_usdt", "largest_winner_share", "min_equity"):
        row[key] = m.get(key)
    row.update({
        "signals": d.get("source_signals_before_execution_filters"),
        "entries": d.get("entry_submissions"), "symbols": d.get("selected_symbols"),
        "source_exit_signals": d.get("source_exit_signals"),
        "trailing_exits": d.get("mbe_trailing_exits"),
        "roi_exits": d.get("mbe_roi_exits"),
        "zero_roi_exits": d.get("source_zero_roi_exits"),
        "global_position_violations": d.get("global_position_violations"),
        "order_rejections": d.get("order_rejections"),
        "real_1m": d.get("real_binance_1m_execution"),
        "exact_shifted_volume": d.get("exact_shifted_volume_mean"),
    })
    return row


def checks(row: dict[str, Any], minimum_trades: int, growth: float) -> dict[str, bool]:
    return {
        "minimum_trades": int(row.get("trades") or 0) >= minimum_trades,
        "growth": float(row.get("geometric_daily_growth") or 0.0) >= growth,
        "positive_expectancy": float(row.get("expectancy_usdt") or 0.0) > 0.0,
        "drawdown_lte_20pct": float(row.get("max_drawdown") or 1.0) <= 0.20,
        "positive_equity": float(row.get("min_equity") or 0.0) > 0.0,
        "one_position": int(row.get("global_position_violations") or 0) == 0,
        "no_rejections": int(row.get("order_rejections") or 0) == 0,
        "real_1m": int(row.get("real_1m") or 0) == 1,
        "shifted_volume": int(row.get("exact_shifted_volume") or 0) == 1,
    }


def ranked(names: list[str], rows: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(names, key=lambda n: (-float(rows[n].get("geometric_daily_growth") or 0.0), -float(rows[n].get("expectancy_usdt") or 0.0), -int(rows[n].get("trades") or 0), n))


def compact(source: Path, destination: Path) -> None:
    if not source.is_dir(): return
    destination.mkdir(parents=True, exist_ok=True)
    for filename in KEEP:
        if (source / filename).is_file(): shutil.copy2(source / filename, destination / filename)


def main() -> int:
    cfgs = configs()
    dev: dict[str, dict[str, Any]] = {}
    for name, cfg in cfgs.items():
        row = read(name, "development", run(name, cfg, "development", DEVELOPMENT))
        row["checks"] = checks(row, 7, 0.0) if row.get("produced") else {}
        row["gate_pass"] = bool(row["checks"]) and all(row["checks"].values())
        dev[name] = row
    eligible = ranked([n for n, r in dev.items() if r.get("gate_pass")], dev)
    survivors = eligible[:2]
    development = {"comparison": dev, "eligible": eligible, "survivors": survivors}

    hold_rows: dict[str, dict[str, Any]] = {}
    for name in survivors:
        row = read(name, "holdout", run(name, cfgs[name], "holdout", HOLDOUT))
        row["checks"] = checks(row, 7, 0.0) if row.get("produced") else {}
        row["gate_pass"] = bool(row["checks"]) and all(row["checks"].values())
        hold_rows[name] = row
    hold_eligible = ranked([n for n, r in hold_rows.items() if r.get("gate_pass")], hold_rows)
    selected = hold_eligible[0] if hold_eligible else None
    holdout = {"comparison": hold_rows, "eligible": hold_eligible, "selected": selected}

    continuous: dict[str, Any] = {"produced": False, "project_gate_pass": False}
    if selected:
        continuous = read(selected, "continuous-30d", run(selected, cfgs[selected], "continuous-30d", CONTINUOUS))
        continuous["checks"] = checks(continuous, 30, 0.01)
        continuous["project_gate_pass"] = all(continuous["checks"].values())
    decision = "PASS_30D_PROJECT_GATE" if continuous.get("project_gate_pass") else "REJECT_OR_COMBINE"
    result = {
        "candidate": "candidate-55", "family": "public_UTBotAlert_OnlyUT_1h",
        "decision": decision, "selected_variant": selected,
        "development": development, "holdout": holdout, "continuous_30d": continuous,
        "source_claim_accepted_as_evidence": False,
        "source_protections_initially_omitted": True,
        "production_ready": False, "long_horizon_run": False,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    dump(EVIDENCE / "RESULT.json", result)
    shutil.copy2(WORK / "manifest.json", EVIDENCE / "manifest.json")
    dump(EVIDENCE / "development" / "comparison.json", development)
    dump(EVIDENCE / "holdout" / "assessment.json", holdout)
    for name in VARIANTS:
        compact(root(name, "development"), EVIDENCE / "development" / name)
        compact(root(name, "holdout"), EVIDENCE / "holdout" / name)
    if selected: compact(root(selected, "continuous-30d"), EVIDENCE / "continuous")
    dump(ARTIFACTS / "utbot-1h-v1-final-result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
