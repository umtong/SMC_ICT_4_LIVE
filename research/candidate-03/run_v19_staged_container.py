#!/usr/bin/env python3
"""Run frozen V19 staged native evidence inside the pinned job image."""
from __future__ import annotations

import base64
import importlib.util
import json
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
NOVEL_STATES = {
    "EXECUTED_FLOW_VACUUM_CONTINUATION",
    "EXECUTED_FLOW_ABSORPTION_CHOCH_REVERSAL",
}


def load_base():
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{BASE_PATH}?ref={REF}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v19-staged-container",
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


def prepare_schedule(week: str, prepared: Path, output: Path) -> int:
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
    shutil.copy2(prepared / "signals.json", output / "signals.json")
    shutil.copy2(prepared / "data_manifest.json", output / "data_manifest.json")
    signals = json.loads((prepared / "signals.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "v19_signal_manifest.json").read_text(encoding="utf-8"))
    preflight = {
        "candidate": manifest["candidate"],
        "week_start": week,
        "engine_status": "causal_opportunity_preflight_only_no_backtest",
        "derived_signal_count": len(signals),
        "minimum_required_episodes": 8,
        "opportunity_gate_reachable": len(signals) >= 8,
        "state_counts": manifest["state_counts"],
        "inventory_regime_counts": manifest["inventory_regime_counts"],
        "routing_counts": manifest["routing_counts"],
        "threshold_policy": manifest["threshold_policy"],
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
    status = base.run(
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
    (output / "gate_status.txt").write_text(f"{status}\n", encoding="utf-8")
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
    return status


def select_ablation_state(output: Path) -> str | None:
    summary = json.loads((output / "state_attribution.json").read_text(encoding="utf-8"))
    rows = summary["by_scenario_kind"]
    novel = [
        (state, float(row["native_account_pnl"]))
        for state, row in rows.items()
        if state in NOVEL_STATES and float(row["native_account_pnl"]) < 0.0
    ]
    if novel:
        return min(novel, key=lambda item: item[1])[0]
    negative = [
        (state, float(row["native_account_pnl"]))
        for state, row in rows.items()
        if float(row["native_account_pnl"]) < 0.0
    ]
    return min(negative, key=lambda item: item[1])[0] if negative else None


def run_ablation(
    week: str,
    prepared: Path,
    full_output: Path,
    ablation_output: Path,
    removed_state: str,
) -> int:
    ablation_output.mkdir(parents=True, exist_ok=True)
    full_signals = json.loads((full_output / "signals.json").read_text(encoding="utf-8"))
    kept = [
        signal
        for signal in full_signals
        if str(signal.get("scenario_kind")) != removed_state
    ]
    shutil.copy2(prepared / "signals.json", prepared / "signals-v19-full.json")
    (prepared / "signals.json").write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (ablation_output / "signals.json").write_text(
        json.dumps(kept, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (ablation_output / "ablation_manifest.json").write_text(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v19-state-ablation",
                "week_start": week,
                "ablation": "REMOVE_ONE_SCENARIO_KIND",
                "removed_scenario_kind": removed_state,
                "full_signal_count": len(full_signals),
                "kept_signal_count": len(kept),
                "diagnostic_only": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if not kept:
        return 1
    return run_native(week, prepared, ablation_output)


def commit_results(status: dict[str, Any]) -> str:
    result_root = base.REPO_ROOT / "research/candidate-03/results"
    (result_root / "V19_STAGED_STATUS.json").write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ref = base.github_api("GET", f"/git/ref/heads/{base.BRANCH}")
    parent = ref["object"]["sha"]
    parent_commit = base.github_api("GET", f"/git/commits/{parent}")
    entries: list[dict[str, str]] = []
    for path in sorted(item for item in result_root.rglob("*") if item.is_file()):
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
            "message": "candidate-03: record staged V19 native evidence",
            "tree": tree["sha"],
            "parents": [parent],
        },
    )
    base.github_api(
        "PATCH",
        f"/git/refs/heads/{base.BRANCH}",
        {"sha": commit["sha"], "force": False},
    )
    print(json.dumps({"committed_sha": commit["sha"], **status}, indent=2, sort_keys=True))
    return commit["sha"]


def execute_week(week: str, name: str) -> tuple[int, int, Path, Path]:
    stage = base.REPO_ROOT / f"artifacts/candidate-03/v19-staged/{name}"
    prepared = stage / "prepared"
    output = stage / "output"
    count = prepare_schedule(week, prepared, output)
    if count < 8:
        (output / "gate_status.txt").write_text("1\n", encoding="utf-8")
        return count, 1, prepared, output
    status = run_native(week, prepared, output)
    return count, status, prepared, output


def main() -> int:
    base.fetch_branch()
    base.verify_frozen_sources()
    verify_runtime()
    status: dict[str, Any] = {
        "candidate": "candidate-03-nt-lvcfr-v19-executed-flow-resilience",
        "frozen_source_blobs": FROZEN_BLOBS,
        "completed_stages": [],
    }
    final_status = 1
    failure: tuple[str, str, Path, Path] | None = None
    stage_root = base.REPO_ROOT / "artifacts/candidate-03/v19-staged"
    for week, name in (
        ("2024-01-08", "development-1"),
        ("2025-06-23", "development-2"),
        ("2022-05-16", "validation-3"),
    ):
        count, gate, prepared, output = execute_week(week, name)
        status[f"{name}_signal_count"] = count
        status[f"{name}_passed"] = gate == 0
        status["completed_stages"].append(name)
        if gate != 0:
            failure = (week, name, prepared, output)
            final_status = 1
            break
        final_status = 0

    if failure is not None:
        week, name, prepared, output = failure
        if (output / "state_attribution.json").exists():
            removed = select_ablation_state(output)
            status["ablation_removed_state"] = removed
            if removed is not None:
                ablation = output.parent / "ablation-output"
                ablation_status = run_ablation(week, prepared, output, ablation, removed)
                status["ablation_passed"] = ablation_status == 0
                status["ablation_stage"] = name

    results = base.REPO_ROOT / "research/candidate-03/results"
    for name in ("development-1", "development-2", "validation-3"):
        output = stage_root / name / "output"
        if output.exists():
            base.copy_results(output, results / f"v19-{name}")
        ablation = stage_root / name / "ablation-output"
        if ablation.exists():
            base.copy_results(ablation, results / f"v19-{name}-ablation")
    commit_results(status)
    return final_status


base.verify_runtime = verify_runtime
base.prepare_schedule = prepare_schedule
base.run_native = run_native
base.select_ablation_state = select_ablation_state
base.run_ablation = run_ablation
raise SystemExit(main())
