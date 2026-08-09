#!/usr/bin/env python3
"""Adaptive Candidate 57 campaign over the reused NautilusTrader Picasso stack."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
C51 = ROOT / "research" / "candidate-51"
BASE_CONFIG = C51 / "config.json"
WORK = ROOT / ".work" / "candidate-57-picasso-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57"
EVIDENCE = HERE / "evidence"
CACHE = ROOT / ".cache" / "candidate-57-picasso-v1"

SOURCE_VARIANT = "exact_level_short"
PROJECT_VARIANTS = ("exact_edge_short", "exact_edge")

LEGACY_KEYS = {
    "sma_offset_low",
    "sma_offset_high",
    "sma_stop_min_fraction",
    "sma_stop_max_fraction",
    "sma_stop_atr_buffer",
}

COMMON_STRATEGY = {
    "cooldown_minutes": 0,
    "max_hold_minutes": 2400,
    "funding_flatten_minute": 60,
    "funding_blackout_before_minutes": -1,
    "funding_blackout_after_minutes": -1,
    "picasso_bucket_minutes": 60,
    "picasso_adx_period": 14,
    "picasso_rsi_long_period": 22,
    "picasso_rsi_short_period": 17,
    "picasso_bb_long_period": 16,
    "picasso_bb_short_period": 20,
    "picasso_volume_long_period": 38,
    "picasso_volume_short_period": 20,
    "picasso_adx_long_min_1": 5.7,
    "picasso_adx_long_max_1": 6.5,
    "picasso_adx_long_min_2": 20.9,
    "picasso_adx_long_max_2": 50.7,
    "picasso_adx_short_min_1": 9.9,
    "picasso_adx_short_max_1": 21.4,
    "picasso_adx_short_min_2": 30.3,
    "picasso_adx_short_max_2": 50.8,
    "picasso_source_effective_leverage": 5.0,
    "picasso_source_stoploss": 0.317,
    "picasso_trailing_positive": 0.010,
    "picasso_trailing_offset": 0.022,
    "picasso_emergency_target_fraction": 0.10,
    "picasso_roi_0": 0.184,
    "picasso_roi_416": 0.140,
    "picasso_roi_933": 0.073,
    "picasso_roi_1982": 0.0,
    "picasso_atr_period": 20,
    "picasso_ema_long_exit": 91,
    "picasso_ema_short_exit": 147,
    "picasso_atr_long_multiple": 3.8,
    "picasso_atr_short_multiple": 5.0,
    "picasso_volume_long_exit": 19,
    "picasso_volume_short_exit": 41,
}

METRIC_KEYS = (
    "evaluation_start",
    "evaluation_end",
    "calendar_days",
    "starting_nav",
    "ending_nav",
    "total_return",
    "geometric_daily_growth",
    "max_drawdown",
    "min_equity",
    "trades",
    "wins",
    "losses",
    "win_rate",
    "gross_profit",
    "gross_loss",
    "profit_factor",
    "expectancy_usdt",
    "active_days",
    "largest_winner_share",
    "position_counts_by_symbol",
    "gate_checks",
    "gate_pass",
)

DIAGNOSTIC_KEYS = (
    "candidate",
    "external_source",
    "picasso_precedence_mode",
    "picasso_bucket_minutes",
    "source_level_reentry_tested",
    "complete_universe_minutes",
    "quarter_hour_decisions",
    "source_signals_before_execution_filters",
    "entry_submissions",
    "entry_fills",
    "entry_expirations",
    "order_rejections",
    "global_position_violations",
    "max_simultaneous_entry_intents",
    "max_open_positions_observed",
    "used_episode_rejections",
    "cooldown_rejections",
    "funding_runway_rejections",
    "picasso_trailing_activations",
    "picasso_trailing_exits",
    "picasso_roi_exits",
    "picasso_source_signal_exits",
    "selected_symbols",
    "route_counts",
    "actionable_family_counts",
    "unresolved_reason_counts",
)


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date
    minimum_screen_trades: int
    project_gate: bool = False

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


STAGES = {
    "short": Stage(
        "short-development",
        date.fromisoformat("2026-07-22"),
        date.fromisoformat("2026-07-28"),
        minimum_screen_trades=4,
    ),
    "confirmation": Stage(
        "frozen-confirmation",
        date.fromisoformat("2025-11-03"),
        date.fromisoformat("2025-11-09"),
        minimum_screen_trades=4,
    ),
    "intermediate": Stage(
        "intermediate-continuous-30d",
        date.fromisoformat("2025-09-01"),
        date.fromisoformat("2025-09-30"),
        minimum_screen_trades=30,
        project_gate=True,
    ),
    "long": Stage(
        "long-continuous-180d",
        date.fromisoformat("2024-03-01"),
        date.fromisoformat("2024-08-27"),
        minimum_screen_trades=180,
        project_gate=True,
    ),
}


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _subset(mapping: dict[str, Any] | None, keys: Iterable[str]) -> dict[str, Any]:
    if not mapping:
        return {}
    return {key: mapping.get(key) for key in keys if key in mapping}


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_configs() -> dict[str, Path]:
    source = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    config_dir = WORK / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Path] = {}
    for variant in (SOURCE_VARIANT, *PROJECT_VARIANTS):
        config = json.loads(json.dumps(source))
        strategy = config.setdefault("strategy", {})
        for key in LEGACY_KEYS:
            strategy.pop(key, None)
        strategy.update(COMMON_STRATEGY)
        strategy["picasso_precedence_mode"] = variant
        path = config_dir / f"{variant}.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result[variant] = path
    return result


def _mechanical_reasons(
    return_code: int,
    metrics: dict[str, Any] | None,
    diagnostics: dict[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    if return_code != 0:
        reasons.append(f"RUN_RETURN_CODE_{return_code}")
    if metrics is None:
        reasons.append("METRICS_MISSING_OR_INVALID")
    if diagnostics is None:
        reasons.append("DIAGNOSTICS_MISSING_OR_INVALID")
    if metrics is None or diagnostics is None:
        return reasons

    if int(diagnostics.get("order_rejections") or 0) > 0:
        reasons.append("ORDER_REJECTION")
    if int(diagnostics.get("global_position_violations") or 0) > 0:
        reasons.append("GLOBAL_POSITION_VIOLATION")
    if int(diagnostics.get("max_open_positions_observed") or 0) > 1:
        reasons.append("MULTIPLE_OPEN_POSITIONS")
    if int(diagnostics.get("max_simultaneous_entry_intents") or 0) > 1:
        reasons.append("MULTIPLE_ENTRY_INTENTS")

    unresolved = diagnostics.get("unresolved_reason_counts") or {}
    if isinstance(unresolved, dict) and int(unresolved.get("FUTURE_FEATURE_REJECTED") or 0) > 0:
        reasons.append("FUTURE_FEATURE_REJECTED")

    checks = metrics.get("gate_checks") or {}
    if isinstance(checks, dict):
        mechanical_checks = {
            "positive_nav": "NON_POSITIVE_NAV",
            "no_liquidation": "LIQUIDATION",
            "no_order_rejections": "ORDER_REJECTION_CHECK_FAILED",
            "single_entry_intent": "ENTRY_INTENT_CONTRACT_FAILED",
            "single_position": "SINGLE_POSITION_CONTRACT_FAILED",
            "no_global_position_violation": "GLOBAL_SLOT_CONTRACT_FAILED",
            "nautilus_positions_match": "POSITION_REPORT_MISMATCH",
        }
        for key, reason in mechanical_checks.items():
            if key in checks and not bool(checks[key]):
                reasons.append(reason)

    for key in ("ending_nav", "geometric_daily_growth", "max_drawdown", "expectancy_usdt"):
        if key in metrics and not _finite_number(metrics.get(key)):
            reasons.append(f"NONFINITE_{key.upper()}")

    return sorted(set(reasons))


def _logic_reasons(
    metrics: dict[str, Any] | None,
    diagnostics: dict[str, Any] | None,
    *,
    minimum_trades: int,
    require_project_gate: bool,
) -> list[str]:
    if metrics is None or diagnostics is None:
        return []
    reasons: list[str] = []
    signals = int(diagnostics.get("source_signals_before_execution_filters") or 0)
    entries = int(diagnostics.get("entry_submissions") or 0)
    trades = int(metrics.get("trades") or 0)
    expectancy = float(metrics.get("expectancy_usdt") or 0.0)
    growth = float(metrics.get("geometric_daily_growth") or 0.0)
    drawdown = float(metrics.get("max_drawdown") or 0.0)

    if signals <= 0:
        reasons.append("NO_SOURCE_SIGNAL_IN_MARKET_PERIOD")
    elif entries <= 0:
        reasons.append("SOURCE_SIGNALS_BUT_NO_ENTRY_SUBMISSION")
    if trades <= 0:
        reasons.append("NO_COMPLETED_TRADE")
    if trades < minimum_trades:
        reasons.append(f"INSUFFICIENT_INDEPENDENT_TRADES_{trades}_LT_{minimum_trades}")
    if expectancy <= 0.0:
        reasons.append("NON_POSITIVE_EXPECTANCY")
    if growth <= 0.0:
        reasons.append("NON_POSITIVE_GEOMETRIC_GROWTH")
    if drawdown > 0.20:
        reasons.append("MAX_DRAWDOWN_GT_20PCT")
    if require_project_gate and growth < 0.01:
        reasons.append("GEOMETRIC_DAILY_GROWTH_LT_1PCT")
    return sorted(set(reasons))


def classify_case(
    *,
    variant: str,
    stage: Stage,
    return_code: int,
    metrics: dict[str, Any] | None,
    diagnostics: dict[str, Any] | None,
) -> dict[str, Any]:
    mechanical = _mechanical_reasons(return_code, metrics, diagnostics)
    logic = _logic_reasons(
        metrics,
        diagnostics,
        minimum_trades=stage.minimum_screen_trades,
        require_project_gate=stage.project_gate,
    )
    source_level = variant == SOURCE_VARIANT
    mechanics_ok = not mechanical
    positive_screen = mechanics_ok and not [
        reason
        for reason in logic
        if reason
        in {
            "NO_SOURCE_SIGNAL_IN_MARKET_PERIOD",
            "SOURCE_SIGNALS_BUT_NO_ENTRY_SUBMISSION",
            "NO_COMPLETED_TRADE",
            "NON_POSITIVE_EXPECTANCY",
            "NON_POSITIVE_GEOMETRIC_GROWTH",
            "MAX_DRAWDOWN_GT_20PCT",
        }
        or reason.startswith("INSUFFICIENT_INDEPENDENT_TRADES_")
    ]
    project_gate_pass = (
        positive_screen
        and not source_level
        and (not stage.project_gate or float((metrics or {}).get("geometric_daily_growth") or 0.0) >= 0.01)
    )

    if mechanical:
        failure_class = "IMPLEMENTATION_OR_INTEGRATION_ERROR"
    elif source_level and positive_screen:
        failure_class = "SOURCE_ALPHA_PROBE_PASS_INDEPENDENCE_UNRESOLVED"
    elif logic:
        failure_class = "STRATEGY_LOGIC_FAILURE"
    else:
        failure_class = "PASS"

    return {
        "variant": variant,
        "stage": stage.name,
        "interval": [str(stage.start), str(stage.end)],
        "calendar_days": stage.days,
        "return_code": return_code,
        "mechanics_ok": mechanics_ok,
        "positive_screen": positive_screen,
        "project_gate_pass": project_gate_pass,
        "causal_independence_eligible": not source_level,
        "failure_class": failure_class,
        "implementation_reasons": mechanical,
        "logic_reasons": logic,
        "metrics": _subset(metrics, METRIC_KEYS),
        "diagnostics": _subset(diagnostics, DIAGNOSTIC_KEYS),
    }


def persist_case(
    *,
    case_id: str,
    assessment: dict[str, Any],
    output: Path,
    log_path: Path,
) -> None:
    payload = dict(assessment)
    payload["raw_evidence"] = {
        "metrics_sha256": _sha256(output / "metrics.json"),
        "diagnostics_sha256": _sha256(output / "strategy_diagnostics.json"),
        "run_manifest_sha256": _sha256(output / "run.json"),
        "data_manifest_sha256": _sha256(output / "data_manifest.json"),
        "log_sha256": _sha256(log_path),
    }
    for name in ("run.json", "data_manifest.json"):
        data = _json(output / name)
        if data is not None:
            payload[name.removesuffix(".json")] = data
    if assessment["return_code"] != 0 and log_path.is_file():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        payload["failure_log_tail"] = lines[-200:]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"{case_id}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_case(
    *,
    variant: str,
    stage: Stage,
    config_path: Path,
) -> dict[str, Any]:
    case_id = f"{stage.name}-{variant}"
    output = ARTIFACTS / case_id
    workspace = WORK / "workspace" / case_id
    log_dir = WORK / "logs"
    log_path = log_dir / f"{case_id}.log"
    if output.exists():
        shutil.rmtree(output)
    if workspace.exists():
        shutil.rmtree(workspace)
    output.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(config_path),
        "--start",
        str(stage.start),
        "--end",
        str(stage.end),
        "--cache",
        str(CACHE),
        "--output",
        str(output),
        "--workspace",
        str(workspace),
    ]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(C51)
    with log_path.open("w", encoding="utf-8") as log:
        log.write("COMMAND " + " ".join(command) + "\n")
        log.flush()
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )

    metrics = _json(output / "metrics.json")
    diagnostics = _json(output / "strategy_diagnostics.json")
    assessment = classify_case(
        variant=variant,
        stage=stage,
        return_code=completed.returncode,
        metrics=metrics,
        diagnostics=diagnostics,
    )
    persist_case(
        case_id=case_id,
        assessment=assessment,
        output=output,
        log_path=log_path,
    )
    print(json.dumps({
        "case": case_id,
        "failure_class": assessment["failure_class"],
        "mechanics_ok": assessment["mechanics_ok"],
        "positive_screen": assessment["positive_screen"],
        "project_gate_pass": assessment["project_gate_pass"],
        "metrics": assessment["metrics"],
    }, indent=2, sort_keys=True))
    return assessment


def _rank_project(rows: dict[str, dict[str, Any]]) -> list[str]:
    eligible = [
        variant
        for variant, row in rows.items()
        if variant in PROJECT_VARIANTS
        and row.get("mechanics_ok")
        and row.get("positive_screen")
    ]
    eligible.sort(
        key=lambda variant: (
            -float(rows[variant]["metrics"].get("geometric_daily_growth") or 0.0),
            -float(rows[variant]["metrics"].get("expectancy_usdt") or 0.0),
            -int(rows[variant]["metrics"].get("trades") or 0),
            variant,
        )
    )
    return eligible


def _result_markdown(decision: dict[str, Any]) -> str:
    lines = [
        "# Candidate 57 result",
        "",
        f"- Status: `{decision['status']}`",
        f"- Selected project variant: `{decision.get('selected_project_variant')}`",
        f"- Source level probe confirmed: `{decision.get('source_level_probe_confirmed')}`",
        f"- Highest completed stage: `{decision.get('highest_completed_stage')}`",
        "",
        "## Interpretation",
        "",
        decision["interpretation"],
        "",
        "## Stage decisions",
        "",
    ]
    for key in ("short", "confirmation", "intermediate", "long"):
        value = decision.get("stage_decisions", {}).get(key)
        if value is not None:
            lines.append(f"- `{key}`: `{value}`")
    lines.extend([
        "",
        "The level-reentry source probe is never counted as proof of independent trade frequency. "
        "Only an edge variant can advance through the project gate.",
        "",
    ])
    return "\n".join(lines)


def run_campaign() -> dict[str, Any]:
    for path in (WORK, ARTIFACTS, EVIDENCE):
        path.mkdir(parents=True, exist_ok=True)

    configs = build_configs()
    manifest = {
        "candidate": "candidate-57",
        "family": "public_RSI_BB_MACD_Nov_2023_1h_2_Dec",
        "source_variant": SOURCE_VARIANT,
        "project_variants": list(PROJECT_VARIANTS),
        "stages": {
            key: {
                "name": stage.name,
                "start": str(stage.start),
                "end": str(stage.end),
                "calendar_days": stage.days,
                "minimum_screen_trades": stage.minimum_screen_trades,
                "project_gate": stage.project_gate,
            }
            for key, stage in STAGES.items()
        },
        "contracts": {
            "engine": "NautilusTrader BacktestNode",
            "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "global_pending_or_position_limit": 1,
            "risk_fraction": 0.03,
            "level_reentry_not_accepted_as_independent_frequency": True,
        },
        "code_sha256": {
            "campaign": _sha256(Path(__file__)),
            "materialized_router": _sha256(C51 / "router.py"),
            "materialized_strategy": _sha256(C51 / "strategy.py"),
        },
    }
    (EVIDENCE / "campaign_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    short_rows = {
        variant: run_case(
            variant=variant,
            stage=STAGES["short"],
            config_path=configs[variant],
        )
        for variant in (SOURCE_VARIANT, *PROJECT_VARIANTS)
    }

    project_ranking = _rank_project(short_rows)
    selected_project = project_ranking[0] if project_ranking else None
    source_short_pass = bool(short_rows[SOURCE_VARIANT].get("positive_screen"))

    confirmation_rows: dict[str, dict[str, Any]] = {}
    if source_short_pass:
        confirmation_rows[SOURCE_VARIANT] = run_case(
            variant=SOURCE_VARIANT,
            stage=STAGES["confirmation"],
            config_path=configs[SOURCE_VARIANT],
        )
    if selected_project is not None:
        confirmation_rows[selected_project] = run_case(
            variant=selected_project,
            stage=STAGES["confirmation"],
            config_path=configs[selected_project],
        )

    source_confirmed = bool(
        confirmation_rows.get(SOURCE_VARIANT, {}).get("positive_screen")
    )
    project_confirmed = bool(
        selected_project
        and confirmation_rows.get(selected_project, {}).get("positive_screen")
    )

    intermediate_row: dict[str, Any] | None = None
    if project_confirmed and selected_project is not None:
        intermediate_row = run_case(
            variant=selected_project,
            stage=STAGES["intermediate"],
            config_path=configs[selected_project],
        )

    long_row: dict[str, Any] | None = None
    if intermediate_row and intermediate_row.get("project_gate_pass"):
        long_row = run_case(
            variant=selected_project,
            stage=STAGES["long"],
            config_path=configs[selected_project],
        )

    if long_row and long_row.get("project_gate_pass"):
        status = "PROJECT_TARGET_PASSED_180D_CONTINUOUS"
        interpretation = (
            "The independent rising-edge interpretation passed mechanics, frozen confirmation, "
            "the 30-day project gate, and the 180-day continuous project gate."
        )
        highest = "long"
    elif long_row is not None:
        status = (
            "LONG_IMPLEMENTATION_FAILURE"
            if not long_row.get("mechanics_ok")
            else "LONG_STRATEGY_LOGIC_FAILURE"
        )
        interpretation = (
            "The candidate earned long validation but failed there. The recorded classification "
            "separates a mechanical/integration fault from a valid but inadequate trading policy."
        )
        highest = "long"
    elif intermediate_row is not None:
        status = (
            "INTERMEDIATE_IMPLEMENTATION_FAILURE"
            if not intermediate_row.get("mechanics_ok")
            else "INTERMEDIATE_PROJECT_GATE_FAILURE"
        )
        interpretation = (
            "The independent variant survived short confirmation but did not justify long compute "
            "under the 30-day project gate."
        )
        highest = "intermediate"
    elif project_confirmed:
        status = "CONFIRMED_EDGE_WITHOUT_INTERMEDIATE_RESULT"
        interpretation = (
            "The independent edge survived confirmation, but no valid intermediate result was produced."
        )
        highest = "confirmation"
    elif source_confirmed:
        status = "SOURCE_ALPHA_CONFIRMED_INDEPENDENT_OPPORTUNITY_FAILURE"
        interpretation = (
            "The source-faithful short level behavior remained positive, but no independent edge "
            "variant survived. Repeated source-level re-entry is therefore not accepted as project "
            "trade frequency; the next design must add genuinely independent scenario families or "
            "a causal rearm transition."
        )
        highest = "confirmation"
    else:
        has_mechanical = any(
            not row.get("mechanics_ok")
            for row in [*short_rows.values(), *confirmation_rows.values()]
        )
        status = (
            "IMPLEMENTATION_FAILURE_REQUIRES_REPAIR"
            if has_mechanical
            else "PICASSO_FAMILY_LOGIC_REJECTED"
        )
        interpretation = (
            "No project-eligible independent variant supplied enough positive after-cost evidence "
            "to justify intermediate or long validation."
        )
        highest = "confirmation" if confirmation_rows else "short"

    stage_decisions = {
        "short": {
            "project_ranking": project_ranking,
            "selected_project_variant": selected_project,
            "source_level_pass": source_short_pass,
        },
        "confirmation": {
            "project_confirmed": project_confirmed,
            "source_level_confirmed": source_confirmed,
        },
        "intermediate": (
            {
                "produced": True,
                "project_gate_pass": bool(intermediate_row.get("project_gate_pass")),
                "failure_class": intermediate_row.get("failure_class"),
            }
            if intermediate_row is not None
            else {"produced": False, "reason": "NOT_EARNED"}
        ),
        "long": (
            {
                "produced": True,
                "project_gate_pass": bool(long_row.get("project_gate_pass")),
                "failure_class": long_row.get("failure_class"),
            }
            if long_row is not None
            else {"produced": False, "reason": "NOT_EARNED"}
        ),
    }

    decision = {
        "candidate": "candidate-57",
        "status": status,
        "selected_project_variant": selected_project,
        "source_level_probe_confirmed": source_confirmed,
        "highest_completed_stage": highest,
        "interpretation": interpretation,
        "stage_decisions": stage_decisions,
        "short_assessments": short_rows,
        "confirmation_assessments": confirmation_rows,
        "intermediate_assessment": intermediate_row,
        "long_assessment": long_row,
    }
    (EVIDENCE / "decision.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (EVIDENCE / "RESULT.md").write_text(
        _result_markdown(decision),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": status,
        "selected_project_variant": selected_project,
        "source_level_probe_confirmed": source_confirmed,
        "highest_completed_stage": highest,
    }, indent=2, sort_keys=True))
    return decision


def main() -> None:
    try:
        run_campaign()
    except Exception as exc:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        fatal = {
            "candidate": "candidate-57",
            "status": "CAMPAIGN_FATAL_IMPLEMENTATION_ERROR",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc().splitlines(),
        }
        (EVIDENCE / "fatal_error.json").write_text(
            json.dumps(fatal, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
