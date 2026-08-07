#!/usr/bin/env python3
"""Run independent V23 basis-dislocation compression reversals on fixed BTC weeks.

V23 does not depend on the sparse V19 signal schedule. The controller reuses
only its verified official data preparation and the unchanged NautilusTrader
execution, costs, single-slot portfolio constraint and 3% current-NAV risk
budget. Compact decision evidence is committed to the research branch.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import math
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any

TOKEN = os.environ["GITHUB_TOKEN"]
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
REF = os.environ.get("GITHUB_SHA", "research/candidate-03")
BASE_PATH = "research/candidate-03/run_v18_staged_container.py"
CONFIG = Path("research/candidate-03/nt_lvcfr_v19_config.json")
WEEKS = (
    ("2024-01-08", "development-1"),
    ("2025-06-23", "development-2"),
    ("2022-05-16", "validation-3"),
)
FROZEN_BLOBS = {
    "research/candidate-03/prepare_nt_lvcfr_v19.py": "d9f1946676b661568a224c00c75cc3d73955c367",
    "research/candidate-03/nt_lvcfr_v19_config.json": "651979bbb5458a034c3dd931eef9e33377310b98",
    "research/candidate-03/rebuild_nt_lvcfr_trade_proxy_catalog.py": "68267662f5730238b8115ff1928b5a303e585acb",
    "research/candidate-03/nt_lvcfr_trade_proxy.py": "37f34bf4cb86911e631d43b3aa28df4c7a7aff37",
    "research/candidate-03/nt_lvcfr_data.py": "f096b6dfd3944f559983010c03cd61622ee8c977",
    "research/candidate-03/nt_lvcfr_strategy.py": "e4d00ae0c6fa1d24198c846bccb247baacdc0456",
    "research/candidate-03/run_nt_lvcfr.py": "74bb02f1b69ee31ce32ddfa47497bdd9770ac00b",
}


def load_base():
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{BASE_PATH}?ref={REF}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v23-basis-dislocation-compression",
        },
    )
    target = Path("/tmp/run_v23_staged_base.py")
    with urllib.request.urlopen(request, timeout=120) as response:
        target.write_bytes(response.read())
    spec = importlib.util.spec_from_file_location("v23_base", target)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load staged base controller")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base()
base.CONFIG = CONFIG
base.FROZEN_BLOBS = FROZEN_BLOBS


def verify_runtime() -> None:
    base.run(["smc4", "doctor"])
    for test in (
        "test_nt_lvcfr.py",
        "test_nt_lvcfr_trade_proxy.py",
        "test_nt_lvcfr_v23.py",
    ):
        base.run([base.sys.executable, f"research/candidate-03/{test}"])


def prepare_v23_schedule(
    week: str,
    prepared: Path,
    output: Path,
) -> int:
    prepared.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/prepare_nt_lvcfr_v19.py",
            "--week-start",
            week,
            "--output",
            str(prepared),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "preparation.log",
    )
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/derive_nt_lvcfr_v23_signals.py",
            "--prepared-root",
            str(prepared),
            "--output-signals",
            str(prepared / "signals.json"),
            "--output-manifest",
            str(output / "v23_signal_manifest.json"),
        ],
        log_path=output / "v23_derivation.log",
    )
    signals = json.loads((prepared / "signals.json").read_text(encoding="utf-8"))
    (output / "signals.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = json.loads(
        (output / "v23_signal_manifest.json").read_text(encoding="utf-8")
    )
    preflight = {
        "candidate": "candidate-03-nt-lvcfr-v23-basis-dislocation-compression",
        "week_start": week,
        "engine_status": "independent_causal_opportunity_preflight_no_backtest",
        "v23_signal_count": len(signals),
        "signals_per_day": manifest["signals_per_day"],
        "minimum_required_episodes": 8,
        "opportunity_gate_reachable": len(signals) >= 8,
        "output_state_counts": manifest["output_state_counts"],
        "event_counts": manifest["event_counts"],
        "no_trade_reasons": manifest["no_trade_reasons"],
        "minimum_gross_structural_rr": manifest["minimum_gross_structural_rr"],
        "median_gross_structural_rr": manifest["median_gross_structural_rr"],
        "selection_policy": manifest["selection_policy"],
        "performance_metrics_calculated": False,
    }
    (output / "preflight_summary.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(preflight, indent=2, sort_keys=True))
    return len(signals)


def run_native(week: str, prepared: Path, output: Path) -> int:
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/rebuild_nt_lvcfr_trade_proxy_catalog.py",
            "--prepared-root",
            str(prepared),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "catalog_rebuild.log",
    )
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/run_nt_lvcfr.py",
            "--week-start",
            week,
            "--prepared-root",
            str(prepared),
            "--output",
            str(output),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "backtest.log",
    )
    gate_status = base.run(
        [
            base.sys.executable,
            "research/candidate-03/gate_nt_lvcfr.py",
            str(output / "metrics.json"),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "gate.json",
        check=False,
    )
    (output / "gate_status.txt").write_text(
        f"{gate_status}\n", encoding="utf-8"
    )
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/summarize_nt_lvcfr_states.py",
            "--metrics",
            str(output / "metrics.json"),
            "--episodes",
            str(output / "episodes.csv"),
            "--signals",
            str(output / "signals.json"),
            "--output",
            str(output / "state_attribution.json"),
        ]
    )
    return gate_status


def copy_essential(source: Path, destination: Path) -> list[Path]:
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in (
        "metrics.json",
        "episodes.csv",
        "state_attribution.json",
        "preflight_summary.json",
        "v23_signal_manifest.json",
    ):
        path = source / name
        if path.exists():
            target = destination / name
            shutil.copy2(path, target)
            copied.append(target)
    return copied


def compact_week_result(
    *,
    week: str,
    name: str,
    signal_count: int,
    gate_status: int | None,
    output: Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "week_start": week,
        "stage": name,
        "v23_signal_count": signal_count,
        "official_gate_status": gate_status,
        "official_gate_passed": gate_status == 0,
    }
    if (output / "metrics.json").exists():
        metrics = json.loads(
            (output / "metrics.json").read_text(encoding="utf-8")
        )
        for key in (
            "initial_nav",
            "final_nav",
            "net_return",
            "daily_geometric_growth",
            "max_drawdown",
            "independent_episodes",
            "wins",
            "losses",
            "win_rate",
            "mean_episode_pnl",
            "native_orders",
            "native_positions",
            "entry_rejections",
            "incomplete_at_end",
            "target_met",
        ):
            result[key] = metrics.get(key)
    if (output / "gate.json").exists():
        result["official_gate"] = json.loads(
            (output / "gate.json").read_text(encoding="utf-8")
        )
    if (output / "v23_signal_manifest.json").exists():
        manifest = json.loads(
            (output / "v23_signal_manifest.json").read_text(encoding="utf-8")
        )
        result["signals_per_day"] = manifest["signals_per_day"]
        result["no_trade_reasons"] = manifest["no_trade_reasons"]
        result["event_counts"] = manifest["event_counts"]
        result["output_state_counts"] = manifest["output_state_counts"]
        result["median_gross_structural_rr"] = manifest[
            "median_gross_structural_rr"
        ]
    return result


def aggregate_results(weeks: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in weeks if row.get("final_nav") is not None]
    if not completed:
        return {"completed_week_count": 0}
    log_growth = 0.0
    wins = 0
    episodes = 0
    signals = 0
    for row in completed:
        initial = float(row["initial_nav"])
        final = float(row["final_nav"])
        if initial <= 0.0 or final <= 0.0:
            raise ValueError("non-positive NAV in completed week")
        log_growth += math.log(final / initial)
        wins += int(row.get("wins") or 0)
        episodes += int(row.get("independent_episodes") or 0)
        signals += int(row.get("v23_signal_count") or 0)
    total_days = len(completed) * 7
    return {
        "completed_week_count": len(completed),
        "total_evaluation_days": total_days,
        "aggregate_nav_multiple": math.exp(log_growth),
        "aggregate_daily_geometric_growth": math.exp(log_growth / total_days) - 1.0,
        "pooled_signals": signals,
        "pooled_episodes": episodes,
        "pooled_wins": wins,
        "pooled_win_rate": wins / episodes if episodes else 0.0,
        "worst_week_max_drawdown": max(
            float(row["max_drawdown"]) for row in completed
        ),
        "all_weeks_official_gate_passed": (
            len(completed) == len(WEEKS)
            and all(bool(row["official_gate_passed"]) for row in completed)
        ),
        "all_weeks_target_met": (
            len(completed) == len(WEEKS)
            and all(bool(row.get("target_met")) for row in completed)
        ),
        "all_weeks_positive_expectancy": (
            len(completed) == len(WEEKS)
            and all(
                float(row.get("mean_episode_pnl") or 0.0) > 0.0
                for row in completed
            )
        ),
        "all_weeks_minimum_episodes": (
            len(completed) == len(WEEKS)
            and all(
                int(row.get("independent_episodes") or 0) >= 8
                for row in completed
            )
        ),
    }


def commit_minimal_results(
    status: dict[str, Any],
    files: list[Path],
) -> str:
    result_root = base.REPO_ROOT / "research/candidate-03/results"
    result_root.mkdir(parents=True, exist_ok=True)
    status_path = result_root / "V23_STABILITY_STATUS.json"
    status_path.write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    files = [status_path, *files]
    ref = base.github_api("GET", f"/git/ref/heads/{base.BRANCH}")
    parent = ref["object"]["sha"]
    parent_commit = base.github_api("GET", f"/git/commits/{parent}")
    entries: list[dict[str, str]] = []
    for path in files:
        blob = base.github_api(
            "POST",
            "/git/blobs",
            {
                "content": base64.b64encode(path.read_bytes()).decode(),
                "encoding": "base64",
            },
        )
        entries.append(
            {
                "path": path.relative_to(base.REPO_ROOT).as_posix(),
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )
    tree = base.github_api(
        "POST",
        "/git/trees",
        {"base_tree": parent_commit["tree"]["sha"], "tree": entries},
    )
    commit = base.github_api(
        "POST",
        "/git/commits",
        {
            "message": "candidate-03: record V23 basis-dislocation native evidence",
            "tree": tree["sha"],
            "parents": [parent],
        },
    )
    base.github_api(
        "PATCH",
        f"/git/refs/heads/{base.BRANCH}",
        {"sha": commit["sha"], "force": False},
    )
    return str(commit["sha"])


def main() -> int:
    base.fetch_branch()
    base.verify_frozen_sources()
    verify_runtime()
    stage_root = (
        base.REPO_ROOT / "artifacts/candidate-03/v23-basis-dislocation-stability"
    )
    result_root = base.REPO_ROOT / "research/candidate-03/results"
    status: dict[str, Any] = {
        "candidate": "candidate-03-nt-lvcfr-v23-basis-dislocation-compression",
        "official_gate_unchanged": True,
        "risk_fraction": 0.03,
        "independent_source_detector": True,
        "entry_logic": (
            "causal 4h spot-perpetual basis distribution -> Tukey outer-fence "
            "dislocation -> futures/spot divergence -> frozen-fence reentry -> native entry"
        ),
        "frozen_source_blobs": FROZEN_BLOBS,
        "weeks": [],
    }
    committed_files: list[Path] = []
    for week, name in WEEKS:
        prepared = stage_root / name / "prepared"
        output = stage_root / name / "output"
        signal_count = prepare_v23_schedule(week, prepared, output)
        gate_status: int | None = None
        if signal_count > 0:
            gate_status = run_native(week, prepared, output)
        row = compact_week_result(
            week=week,
            name=name,
            signal_count=signal_count,
            gate_status=gate_status,
            output=output,
        )
        status["weeks"].append(row)
        committed_files.extend(
            copy_essential(output, result_root / f"v23-{name}")
        )

    status["aggregate"] = aggregate_results(status["weeks"])
    aggregate = status["aggregate"]
    promote = (
        aggregate.get("completed_week_count") == len(WEEKS)
        and float(aggregate.get("aggregate_daily_geometric_growth") or 0.0)
        >= 0.01
        and float(aggregate.get("pooled_win_rate") or 0.0) >= 0.45
        and float(aggregate.get("worst_week_max_drawdown") or 1.0) <= 0.20
        and bool(aggregate.get("all_weeks_target_met"))
        and bool(aggregate.get("all_weeks_positive_expectancy"))
        and bool(aggregate.get("all_weeks_minimum_episodes"))
    )
    status["decision"] = (
        "PROMOTE_TO_LONG_HORIZON"
        if promote
        else "REJECT_OR_REDESIGN_V23"
    )
    committed_sha = commit_minimal_results(status, committed_files)
    print(
        json.dumps(
            {"committed_sha": committed_sha, **status},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


raise SystemExit(main())
