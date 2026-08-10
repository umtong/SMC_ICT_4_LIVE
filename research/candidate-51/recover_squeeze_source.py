#!/usr/bin/env python3
"""Recover the immutable public squeeze system and its optimization history.

This is source archaeology, not a performance test.  It answers a narrowly
defined question before any port is attempted:

* What executable rules and parameter sets produced the public multi-year logs?
* Were the four assets governed by one policy or separately optimized policies?
* How much of the reported trade count is repeated use of one causal 4h release?
* Which assumptions must be removed or adapted for the project account?

The workflow downloads the public repository at an immutable commit.  This
script never searches a parameter space and never changes a strategy rule.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any


SOURCE_COMMIT = "99cfa582b239fd9c59a5ac92618a3e36bb73ed76"
ASSET_LOGS = {
    "BTCUSD": "trade_logs/trade_log_btc.txt",
    "ETHUSD": "trade_logs/trade_log_eth.txt",
    "SOLUSD": "trade_logs/trade_log_sol.txt",
    "XRPUSD": "trade_logs/trade_log_xrp.txt",
}
HEADER_PATTERNS = {
    "period_days": re.compile(r"^Period:\s+([0-9]+)\s+days", re.I),
    "trade_tf": re.compile(r"Timeframes:\s*Trade=([^,]+),\s*Signal=([^,]+),\s*ATR=([^\s]+)", re.I),
    "bb": re.compile(r"BB:\s*([0-9]+)\s+period,\s*([0-9.]+)\s+std", re.I),
    "kc": re.compile(r"KC:\s*([0-9]+)\s+period,\s*([0-9.]+)x\s+ATR", re.I),
    "squeeze": re.compile(r"Squeeze:\s*([0-9]+)\+\s*bars", re.I),
    "stops": re.compile(r"Stops:\s*([0-9.]+)x\s*SL,\s*([0-9.]+)x\s*TP", re.I),
    "position": re.compile(r"Position:\s*([0-9.]+)%\s*base,\s*([0-9.]+)%-([0-9.]+)%\s*range", re.I),
    "backtest_period": re.compile(r"^Period:\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", re.I),
}
ENTRY_RE = re.compile(
    r"^ENTRY\s+(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+"
    r"(?P<direction>LONG|SHORT)\s+\$\s*(?P<price>[0-9,]+(?:\.[0-9]+)?)\s+"
    r"(?P<size>[0-9.]+)%\s+"
    r"(?P<reasons>SQ\d+\s+V[-0-9.]+\s+M[-0-9.]+\s+RSI[-0-9.]+)"
)
EXIT_RE = re.compile(
    r"^EXIT\s+(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+"
    r"\$\s*(?P<price>[0-9,]+(?:\.[0-9]+)?)\s+"
    r"\$(?P<pnl>[+-][0-9,]+)"
)
REASON_RE = re.compile(r"SQ(?P<sq>\d+)\s+V(?P<vol>[-0-9.]+)\s+M(?P<mom>[-0-9.]+)\s+RSI(?P<rsi>[-0-9.]+)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    return value


def _distribution_external_value(raw: float, distribution_json: str | None) -> Any:
    """Decode Optuna categorical parameters; numeric distributions are identity."""
    if not distribution_json:
        return raw
    try:
        payload = json.loads(distribution_json)
    except json.JSONDecodeError:
        return raw
    name = str(payload.get("name", ""))
    attrs = payload.get("attributes", {})
    if name == "CategoricalDistribution":
        choices = attrs.get("choices", [])
        index = int(round(raw))
        if 0 <= index < len(choices):
            return choices[index]
    if name == "IntDistribution":
        return int(round(raw))
    return raw


def recover_optuna(db_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = sorted(
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        )
        studies: list[dict[str, Any]] = []
        if not {"studies", "trials"}.issubset(tables):
            return {"tables": tables, "studies": studies, "error": "missing Optuna tables"}

        trial_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(trials)")
        }
        state_column = "state" if "state" in trial_columns else next(
            (column for column in trial_columns if "state" in column.lower()), "state"
        )

        for study in connection.execute(
            "SELECT study_id, study_name FROM studies ORDER BY study_id"
        ):
            study_id = int(study["study_id"])
            trials = list(
                connection.execute(
                    f"SELECT trial_id, number, {state_column} AS state "
                    "FROM trials WHERE study_id=? ORDER BY number",
                    (study_id,),
                )
            )
            trial_ids = [int(row["trial_id"]) for row in trials]
            values_by_trial: dict[int, float] = {}
            if "trial_values" in tables and trial_ids:
                placeholders = ",".join("?" for _ in trial_ids)
                for row in connection.execute(
                    "SELECT trial_id, objective, value FROM trial_values "
                    f"WHERE trial_id IN ({placeholders}) ORDER BY objective",
                    trial_ids,
                ):
                    if int(row["objective"]) == 0 and row["value"] is not None:
                        values_by_trial[int(row["trial_id"])] = float(row["value"])

            complete = [
                row for row in trials
                if str(row["state"]).upper() in {"COMPLETE", "1"}
                and int(row["trial_id"]) in values_by_trial
            ]
            best = max(
                complete,
                key=lambda row: values_by_trial[int(row["trial_id"])],
                default=None,
            )
            params: dict[str, Any] = {}
            raw_params: dict[str, Any] = {}
            if best is not None and "trial_params" in tables:
                for row in connection.execute(
                    "SELECT param_name, param_value, distribution_json "
                    "FROM trial_params WHERE trial_id=? ORDER BY param_name",
                    (int(best["trial_id"]),),
                ):
                    raw = float(row["param_value"])
                    raw_params[str(row["param_name"])] = raw
                    params[str(row["param_name"])] = _distribution_external_value(
                        raw, row["distribution_json"]
                    )
            studies.append(
                {
                    "study_id": study_id,
                    "study_name": str(study["study_name"]),
                    "trial_count": len(trials),
                    "complete_trial_count": len(complete),
                    "best_trial_number": None if best is None else int(best["number"]),
                    "best_trial_id": None if best is None else int(best["trial_id"]),
                    "best_value": None if best is None else values_by_trial[int(best["trial_id"])],
                    "best_params": params,
                    "best_params_internal": raw_params,
                }
            )
        return {"tables": tables, "studies": studies}
    finally:
        connection.close()


@dataclass
class ParsedTrade:
    entry_time: str
    exit_time: str | None
    direction: str
    entry_price: float
    exit_price: float | None
    position_size_fraction: float
    pnl: float | None
    reasons: str
    squeeze_bars: int
    volume_ratio: float
    momentum_atr: float
    rsi: float


def parse_trade_log(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    header: dict[str, Any] = {}
    entries: list[ParsedTrade] = []
    pending: ParsedTrade | None = None
    for line in lines:
        match = HEADER_PATTERNS["period_days"].search(line)
        if match and "period_days" not in header:
            header["period_days"] = int(match.group(1))
        match = HEADER_PATTERNS["trade_tf"].search(line)
        if match:
            header.update({"trade_timeframe": match.group(1).strip(), "signal_timeframe": match.group(2).strip(), "atr_timeframe": match.group(3).strip()})
        match = HEADER_PATTERNS["bb"].search(line)
        if match:
            header.update({"bb_period": int(match.group(1)), "bb_std": float(match.group(2))})
        match = HEADER_PATTERNS["kc"].search(line)
        if match:
            header.update({"kc_period": int(match.group(1)), "kc_atr_mult": float(match.group(2))})
        match = HEADER_PATTERNS["squeeze"].search(line)
        if match:
            header["min_squeeze_bars"] = int(match.group(1))
        match = HEADER_PATTERNS["stops"].search(line)
        if match:
            header.update({"atr_stop_mult": float(match.group(1)), "atr_target_mult": float(match.group(2))})
        match = HEADER_PATTERNS["position"].search(line)
        if match:
            header.update({"base_position_fraction": float(match.group(1)) / 100.0, "min_position_fraction": float(match.group(2)) / 100.0, "max_position_fraction": float(match.group(3)) / 100.0})
        match = HEADER_PATTERNS["backtest_period"].search(line)
        if match:
            header.update({"backtest_start": match.group(1), "backtest_end": match.group(2)})

        entry_match = ENTRY_RE.search(line)
        if entry_match:
            reason_match = REASON_RE.search(entry_match.group("reasons"))
            if reason_match is None:
                continue
            pending = ParsedTrade(
                entry_time=entry_match.group("ts"), exit_time=None,
                direction=entry_match.group("direction"),
                entry_price=float(entry_match.group("price").replace(",", "")),
                exit_price=None,
                position_size_fraction=float(entry_match.group("size")) / 100.0,
                pnl=None, reasons=entry_match.group("reasons"),
                squeeze_bars=int(reason_match.group("sq")),
                volume_ratio=float(reason_match.group("vol")),
                momentum_atr=float(reason_match.group("mom")),
                rsi=float(reason_match.group("rsi")),
            )
            entries.append(pending)
            continue

        exit_match = EXIT_RE.search(line)
        if exit_match and pending is not None and pending.exit_time is None:
            pending.exit_time = exit_match.group("ts")
            pending.exit_price = float(exit_match.group("price").replace(",", ""))
            pending.pnl = float(exit_match.group("pnl").replace(",", ""))
            pending = None

    complete = [entry for entry in entries if entry.exit_time is not None]
    wins = [entry for entry in complete if (entry.pnl or 0.0) > 0.0]
    losses = [entry for entry in complete if (entry.pnl or 0.0) < 0.0]
    clusters: list[list[ParsedTrade]] = []
    for entry in complete:
        if clusters and clusters[-1][-1].reasons == entry.reasons and clusters[-1][-1].direction == entry.direction:
            clusters[-1].append(entry)
        else:
            clusters.append([entry])
    repeated = [
        {"reasons": cluster[0].reasons, "direction": cluster[0].direction, "entries": len(cluster), "entry_times": [item.entry_time for item in cluster]}
        for cluster in clusters if len(cluster) > 1
    ]
    pnl_values = [float(item.pnl or 0.0) for item in complete]
    return {
        "path": str(path), "sha256": _sha256(path), "header": header,
        "trade_count": len(complete), "win_count": len(wins), "loss_count": len(losses),
        "win_rate": None if not complete else len(wins) / len(complete),
        "total_pnl": sum(pnl_values),
        "gross_profit": sum(value for value in pnl_values if value > 0.0),
        "gross_loss": -sum(value for value in pnl_values if value < 0.0),
        "repeated_reason_clusters": repeated,
        "repeated_trade_count_above_one_per_cluster": sum(max(0, len(cluster) - 1) for cluster in clusters),
        "trade_fingerprints": [asdict(item) for item in complete],
    }


def _parameter_consistency(logs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    keys = ("trade_timeframe", "signal_timeframe", "atr_timeframe", "bb_period", "bb_std", "kc_period", "kc_atr_mult", "min_squeeze_bars", "atr_stop_mult", "atr_target_mult", "base_position_fraction", "min_position_fraction", "max_position_fraction")
    table = {key: {asset: payload["header"].get(key) for asset, payload in logs.items()} for key in keys}
    shared = {key: len({json.dumps(value, sort_keys=True) for value in values.values()}) <= 1 for key, values in table.items()}
    return {"parameters_by_asset": table, "shared_parameter_flags": shared, "one_policy_across_assets": all(shared.values())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_root, output = args.source_root, args.output
    output.mkdir(parents=True, exist_ok=True)
    expected = ["LICENSE", "README.md", "signal_generator.py", "technical.py", "backtester.py", "optimize.py", "optimize_lib.py", "permutation_test.py", "optuna_history.db", *ASSET_LOGS.values()]
    missing = [name for name in expected if not (source_root / name).is_file()]
    if missing:
        raise RuntimeError(f"missing immutable source files: {missing}")

    files = {name: {"bytes": (source_root / name).stat().st_size, "sha256": _sha256(source_root / name)} for name in expected}
    logs = {asset: parse_trade_log(source_root / relative) for asset, relative in ASSET_LOGS.items()}
    optuna = recover_optuna(source_root / "optuna_history.db")
    consistency = _parameter_consistency(logs)
    target_studies = [study for study in optuna.get("studies", []) if study["study_name"] in {"tf_4h_1h", "tf_4h_1h_leverage"}]
    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_repository": "jicheolha/crypto-trading-bot",
        "source_commit": SOURCE_COMMIT,
        "license": "MIT",
        "purpose": "immutable-source recovery before causal transfer testing",
        "files": files,
        "optuna": optuna,
        "target_studies": target_studies,
        "asset_logs": logs,
        "parameter_consistency": consistency,
        "project_adaptation_contract": {
            "preserve_for_source_control": ["BB-inside-Keltner compression", "completed 4h squeeze release", "volume confirmation", "ATR-normalized momentum direction fallback", "1h ATR stop and target geometry"],
            "do_not_reuse": ["score-scaled risk", "50-90 percent account exposure", "two simultaneous positions", "same-release repeated entries", "seven-day maximum hold as a day-trading default"],
            "predictions_before_transfer_test": ["one-entry-per-release deduplication removes repeat chains without destroying the first-release opportunity", "source edge, if real, is concentrated in the first intraday expansion leg rather than requiring seven-day holds", "OI build, aligned taker flow, positive basis response and peer breadth separate sponsored continuation from failed release", "OI unwind or flow-price absorption identifies a distinct reversal family rather than a weaker continuation filter"],
            "falsification": ["completed release direction has no after-cost positive path on Binance perpetuals across eras and assets", "profit depends on repeated entries from one release episode", "most expectancy arrives only after the day-trading horizon", "derivatives sponsorship works in only one isolated partition or merely removes trades without preserving gross profit"],
        },
    }
    (output / "RECOVERY.json").write_text(json.dumps(_json_safe(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rows: list[dict[str, Any]] = []
    for asset, payload in logs.items():
        rows.append({"asset": asset, **payload["header"], "trades": payload["trade_count"], "wins": payload["win_count"], "losses": payload["loss_count"], "win_rate": payload["win_rate"], "gross_profit": payload["gross_profit"], "gross_loss": payload["gross_loss"], "repeat_excess": payload["repeated_trade_count_above_one_per_cluster"]})
    fieldnames = sorted({key for row in rows for key in row})
    with (output / "ASSET_SUMMARY.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(rows)

    md = ["# Recovered public squeeze system", "", f"- immutable source: `jicheolha/crypto-trading-bot@{SOURCE_COMMIT}`", "- purpose: recover exact executable rules and optimization provenance before transfer", f"- Optuna studies recovered: {len(optuna.get('studies', []))}", f"- one shared parameter policy across BTC/ETH/SOL/XRP: `{consistency['one_policy_across_assets']}`", "", "## Published asset logs", "", "| asset | trades | wins | losses | win % | repeated entries beyond first matching setup | signal / ATR | BB | KC | stop / target |", "|---|---:|---:|---:|---:|---:|---|---|---|---|"]
    for row in rows:
        win = "na" if row.get("win_rate") is None else f"{100.0 * float(row['win_rate']):.1f}"
        md.append("| {asset} | {trades} | {wins} | {losses} | {win} | {repeat} | {signal}/{atr} | {bbp}/{bbs} | {kcp}/{kcm} | {sl}/{tp} |".format(asset=row["asset"], trades=row["trades"], wins=row["wins"], losses=row["losses"], win=win, repeat=row["repeat_excess"], signal=row.get("signal_timeframe", "na"), atr=row.get("atr_timeframe", "na"), bbp=row.get("bb_period", "na"), bbs=row.get("bb_std", "na"), kcp=row.get("kc_period", "na"), kcm=row.get("kc_atr_mult", "na"), sl=row.get("atr_stop_mult", "na"), tp=row.get("atr_target_mult", "na")))
    md.extend(["", "## Recovered 4h / 1h optimization study", ""])
    if target_studies:
        for study in target_studies:
            md.extend([f"### `{study['study_name']}`", "", f"- trials: {study['trial_count']} ({study['complete_trial_count']} complete)", f"- best trial: {study['best_trial_number']}", f"- best value: {study['best_value']}", "", "```json", json.dumps(study["best_params"], indent=2, sort_keys=True), "```", ""])
    else:
        md.append("No exact `tf_4h_1h` study was present; inspect `RECOVERY.json` for all studies.")
    md.extend(["## Causal transfer contract", "", "The next experiment is not a parameter tournament. It freezes the recovered source and asks whether the completed 4h compression-release episode has a stable first-leg edge on Binance perpetuals. One release may create at most one independent entry. Price-only source behavior is measured first; OI, taker flow, basis and peer breadth are then used only to distinguish sponsored continuation, failed breakout and exhaustion reversal.", "", "A negative aggregate result does not automatically discard every component. The experiment must separately report the opportunity engine, first-leg winner engine, repeated-entry contribution, stale multi-day holding contribution, and the state variables associated with losses and missed opportunities.", ""])
    (output / "RECOVERY.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"output": str(output), "studies": len(optuna.get("studies", [])), "assets": list(logs)}, indent=2))


if __name__ == "__main__":
    main()
