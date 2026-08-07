#!/usr/bin/env python3
"""Cross-week native stability test for the frozen V19 measured-acceptance core.

The first frozen week showed that EXECUTED_FLOW_VACUUM_CONTINUATION was a
late-chase state (four native episodes, four losses), while the existing
MEASURED_ACCEPTANCE_CONTINUATION state retained positive after-cost expectancy.
This controller does not tune thresholds or execution. It filters the frozen
V19 schedule to that one structural state and evaluates the three predeclared
BTC weeks through the unchanged NautilusTrader path. Official gates remain
unchanged and are reported rather than weakened.
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
CORE_STATE = "MEASURED_ACCEPTANCE_CONTINUATION"
WEEKS = (
    ("2024-01-08", "development-1"),
    ("2025-06-23", "development-2"),
    ("2022-05-16", "validation-3"),
)
FROZEN_BLOBS = {
    "research/candidate-03/derive_nt_lvcfr_v19_signals.py": "5b2737908fc5dc5d181979adc4a5aa63ed5c56fd",
    "research/candidate-03/prepare_nt_lvcfr_v19.py": "d9f1946676b661568a224c00c75cc3d73955c367",
    "research/candidate-03/nt_lvcfr_v19_config.json": "651979bbb5458a034c3dd931eef9e33377310b98",
    "research/candidate-03/test_nt_lvcfr_v19.py": "9f89910ebeb4fb5bbc8fab0ab4b590f24eca14b8",
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
            "User-Agent": "candidate-03-v19-core-stability",
        },
    )
    target = Path("/tmp/run_v19_staged_base.py")
    with urllib.request.urlopen(request, timeout=120) as response:
        target.write_bytes(response.read())
    spec = importlib.util.spec_from_file_location("v19_base", target)
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
        "test_nt_lvcfr_v13.py",
        "test_nt_lvcfr_v19.py",
    ):
        base.run([base.sys.executable, f"research/candidate-03/{test}"])


def prepare_core_schedule(week: str, prepared: Path, output: Path) -> tuple[int, int]:
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
    shutil.copy2(prepared / "signals.json", prepared / "signals-v1.json")
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/derive_nt_lvcfr_v19_signals.py",
            "--prepared-root",
            str(prepared),
            "--output-manifest",
            str(output / "v19_signal_manifest.json"),
        ],
        log_path=output / "derivation.log",
    )
    full_signals = json.loads((prepared / "signals.json").read_text(encoding="utf-8"))
    core_signals = [
        signal
        for signal in full_signals
        if str(signal.get("scenario_kind")) == CORE_STATE
    ]
    (prepared / "signals.json").write_text(
        json.dumps(core_signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "signals.json").write_text(
        json.dumps(core_signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = json.loads((output / "v19_signal_manifest.json").read_text(encoding="utf-8"))
    regime_counts: dict[str, int] = {}
    for signal in core_signals:
        regime = str(signal.get("details", {}).get("inventory_regime", "UNKNOWN"))
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
    preflight = {
        "candidate": "candidate-03-v19-measured-acceptance-core-stability",
        "week_start": week,
        "engine_status": "causal_core_state_preflight_only_no_backtest",
        "core_state": CORE_STATE,
        "full_v19_signal_count": len(full_signals),
        "core_signal_count": len(core_signals),
        "minimum_required_episodes": 8,
        "opportunity_gate_reachable": len(core_signals) >= 8,
        "core_inventory_regime_counts": dict(sorted(regime_counts.items())),
        "full_state_counts": manifest["state_counts"],
        "threshold_policy": manifest["threshold_policy"],
        "selection_basis": "frozen first-week native state attribution; no threshold or return-fit search",
        "performance_metrics_calculated": False,
    }
    (output / "preflight_summary.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(preflight, indent=2, sort_keys=True))
    return len(full_signals), len(core_signals)


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
    (output / "gate_status.txt").write_text(f"{gate_status}\n", encoding="utf-8")
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
    ):
        path = source / name
        if path.exists():
            target = destination / name
            shutil.copy2(path, target)
            copied.append(target)
    return copied


def commit_minimal_results(status: dict[str, Any], files: list[Path]) -> str:
    result_root = base.REPO_ROOT / "research/candidate-03/results"
    result_root.mkdir(parents=True, exist_ok=True)
    status_path = result_root / "V19_CORE_STABILITY_STATUS.json"
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
            "message": "candidate-03: record V19 core cross-week native evidence",
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


def compact_week_result(
    *,
    week: str,
    name: str,
    full_count: int,
    core_count: int,
    gate_status: int | None,
    output: Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "week_start": week,
        "stage": name,
        "full_v19_signal_count": full_count,
        "core_signal_count": core_count,
        "official_gate_status": gate_status,
        "official_gate_passed": gate_status == 0,
    }
    if (output / "metrics.json").exists():
        metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
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
    return result


def aggregate_results(weeks: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in weeks if row.get("final_nav") is not None]
    if not completed:
        return {"completed_week_count": 0}
    log_growth = 0.0
    total_days = 0
    wins = 0
    episodes = 0
    for row in completed:
        initial = float(row["initial_nav"])
        final = float(row["final_nav"])
        if initial <= 0.0 or final <= 0.0:
            raise ValueError("non-positive NAV in completed week")
        log_growth += math.log(final / initial)
        total_days += 7
        wins += int(row.get("wins") or 0)
        episodes += int(row.get("independent_episodes") or 0)
    return {
        "completed_week_count": len(completed),
        "total_evaluation_days": total_days,
        "aggregate_nav_multiple": math.exp(log_growth),
        "aggregate_daily_geometric_growth": math.exp(log_growth / total_days) - 1.0,
        "pooled_episodes": episodes,
        "pooled_wins": wins,
        "pooled_win_rate": wins / episodes if episodes else 0.0,
        "worst_week_max_drawdown": max(float(row["max_drawdown"]) for row in completed),
        "all_weeks_official_gate_passed": all(bool(row["official_gate_passed"]) for row in completed),
        "all_weeks_target_met": all(bool(row.get("target_met")) for row in completed),
        "all_weeks_positive_expectancy": all(float(row.get("mean_episode_pnl") or 0.0) > 0.0 for row in completed),
        "all_weeks_minimum_episodes": all(int(row.get("independent_episodes") or 0) >= 8 for row in completed),
    }


def main() -> int:
    base.fetch_branch()
    base.verify_frozen_sources()
    verify_runtime()
    stage_root = base.REPO_ROOT / "artifacts/candidate-03/v19-core-stability"
    result_root = base.REPO_ROOT / "research/candidate-03/results"
    status: dict[str, Any] = {
        "candidate": "candidate-03-v19-measured-acceptance-core-stability",
        "diagnostic_only": True,
        "core_state": CORE_STATE,
        "official_gate_unchanged": True,
        "frozen_source_blobs": FROZEN_BLOBS,
        "weeks": [],
    }
    committed_files: list[Path] = []
    for week, name in WEEKS:
        prepared = stage_root / name / "prepared"
        output = stage_root / name / "output"
        full_count, core_count = prepare_core_schedule(week, prepared, output)
        gate_status: int | None = None
        if core_count >= 8:
            gate_status = run_native(week, prepared, output)
        row = compact_week_result(
            week=week,
            name=name,
            full_count=full_count,
            core_count=core_count,
            gate_status=gate_status,
            output=output,
        )
        status["weeks"].append(row)
        committed_files.extend(
            copy_essential(output, result_root / f"v19-core-{name}")
        )
    status["aggregate"] = aggregate_results(status["weeks"])
    aggregate = status["aggregate"]
    if aggregate.get("all_weeks_official_gate_passed"):
        status["decision"] = "PROMOTE_TO_LONG_HORIZON"
    elif (
        aggregate.get("completed_week_count") == len(WEEKS)
        and aggregate.get("all_weeks_target_met")
        and aggregate.get("all_weeks_positive_expectancy")
        and aggregate.get("all_weeks_minimum_episodes")
    ):
        status["decision"] = "STRUCTURAL_EDGE_PRESENT_BUT_OFFICIAL_GATE_NOT_MET"
    else:
        status["decision"] = "REJECT_OR_REDESIGN_CORE_STATE"
    committed_sha = commit_minimal_results(status, committed_files)
    print(json.dumps({"committed_sha": committed_sha, **status}, indent=2, sort_keys=True))
    return 0


raise SystemExit(main())
