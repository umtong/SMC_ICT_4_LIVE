#!/usr/bin/env python3
"""Run V31 nonlinear walk-forward auction models on frozen unseen BTC weeks."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import types
import urllib.request
from pathlib import Path
from typing import Any

TOKEN = os.environ["GITHUB_TOKEN"]
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
REF = os.environ.get("GITHUB_SHA", "research/candidate-03")
V28_CONTROLLER = "research/candidate-03/run_v28_staged_container.py"
CONFIG = Path("research/candidate-03/nt_lvcfr_v31_config.json")
WEEKS = (
    ("2023-10-16", "unseen-1"),
    ("2024-08-19", "unseen-2"),
    ("2022-02-07", "unseen-3"),
)


def fetch_raw(path: str) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{path}?ref={REF}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v31-walk-forward-auction",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8")


def load_controller() -> types.ModuleType:
    source = fetch_raw(V28_CONTROLLER)
    terminal = "raise SystemExit(main())"
    if terminal not in source:
        raise RuntimeError("V28 terminal call not found")
    module = types.ModuleType("candidate03_v28_controller")
    module.__file__ = V28_CONTROLLER
    exec(compile(source.rsplit(terminal, 1)[0], V28_CONTROLLER, "exec"), module.__dict__)
    return module


controller = load_controller()
base = controller.base
controller.CONFIG = CONFIG
controller.WEEKS = WEEKS
controller.v24.CONFIG = CONFIG
base.CONFIG = CONFIG


def verify_runtime() -> str:
    base.run(["smc4", "doctor"])
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/apply_nt_lvcfr_cost_viability_patch.py",
            "research/candidate-03/nt_lvcfr_strategy.py",
        ]
    )
    for test in (
        "test_nt_lvcfr.py",
        "test_nt_lvcfr_trade_proxy.py",
        "test_nt_lvcfr_cost_viability.py",
        "test_nt_lvcfr_v31.py",
    ):
        base.run([base.sys.executable, f"research/candidate-03/{test}"])
    return hashlib.sha256(
        (base.REPO_ROOT / "research/candidate-03/nt_lvcfr_strategy.py").read_bytes()
    ).hexdigest()


def prepare_schedule(week: str, prepared: Path, output: Path) -> int:
    prepared.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/prepare_nt_lvcfr_v19.py",
            "--week-start", week,
            "--output", str(prepared),
            "--config", str(CONFIG),
        ],
        log_path=output / "preparation.log",
    )
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/derive_nt_lvcfr_v31_signals.py",
            "--week-start", week,
            "--prepared-root", str(prepared),
            "--output-manifest", str(output / "v31_signal_manifest.json"),
        ],
        log_path=output / "v31_derivation.log",
    )
    signals = json.loads((prepared / "signals.json").read_text(encoding="utf-8"))
    (prepared / "signals-v28-full.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "signals.json").write_text(
        json.dumps(signals, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = json.loads((output / "v31_signal_manifest.json").read_text(encoding="utf-8"))
    preflight = {
        "candidate": "candidate-03-nt-lvcfr-v31-walk-forward-auction-forest",
        "week_start": week,
        "fit_start": manifest["fit_start"],
        "calibration_start": manifest["calibration_start"],
        "evaluation_start": manifest["evaluation_start"],
        "sample_counts": manifest["sample_counts"],
        "long_model": manifest["long_model"],
        "short_model": manifest["short_model"],
        "v31_signal_count": len(signals),
        "signals_per_day": manifest["signals_per_day"],
        "state_counts": manifest["state_counts"],
        "no_trade_reasons": manifest["no_trade_reasons"],
        "minimum_required_episodes": 8,
        "opportunity_gate_reachable": len(signals) >= 8,
        "target_net_r": manifest["target_net_r"],
        "performance_metrics_calculated": False,
    }
    (output / "preflight_summary.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(preflight, indent=2, sort_keys=True))
    return len(signals)


def copy_essential(source: Path, destination: Path) -> list[Path]:
    destination = Path(str(destination).replace("v28-", "v31-"))
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in (
        "metrics.json", "gate.json", "episodes.csv", "positions.csv",
        "state_attribution.json", "preflight_summary.json", "v31_signal_manifest.json",
    ):
        path = source / name
        if path.exists():
            target = destination / name
            shutil.copy2(path, target)
            copied.append(target)
    return copied


def commit_results(status: dict[str, Any], files: list[Path]) -> str:
    status["candidate"] = "candidate-03-nt-lvcfr-v31-walk-forward-auction-forest"
    status["week_selection_salt"] = "candidate-03|V31|walk-forward-forest|unseen-freeze"
    status["selection_contract"] = (
        "Fixed 180-day causal training and 30-day calibration. One worst negative "
        "direction-state ablation is allowed only on unseen week 1; weeks 2 and 3 "
        "use the resulting frozen grammar."
    )
    root = base.REPO_ROOT / "research/candidate-03/results"
    root.mkdir(parents=True, exist_ok=True)
    status_path = root / "V31_STABILITY_STATUS.json"
    status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files = [status_path, *files]
    ref = base.github_api("GET", f"/git/ref/heads/{base.BRANCH}")
    parent = ref["object"]["sha"]
    parent_commit = base.github_api("GET", f"/git/commits/{parent}")
    entries = []
    for path in files:
        blob = base.github_api(
            "POST", "/git/blobs",
            {"content": base64.b64encode(path.read_bytes()).decode(), "encoding": "base64"},
        )
        entries.append({
            "path": path.relative_to(base.REPO_ROOT).as_posix(),
            "mode": "100644", "type": "blob", "sha": blob["sha"],
        })
    tree = base.github_api(
        "POST", "/git/trees",
        {"base_tree": parent_commit["tree"]["sha"], "tree": entries},
    )
    commit = base.github_api(
        "POST", "/git/commits",
        {"message": "candidate-03: record V31 unseen-week evidence", "tree": tree["sha"], "parents": [parent]},
    )
    base.github_api(
        "PATCH", f"/git/refs/heads/{base.BRANCH}",
        {"sha": commit["sha"], "force": False},
    )
    return str(commit["sha"])


controller.verify_runtime = verify_runtime
controller.prepare_schedule = prepare_schedule
controller.copy_essential = copy_essential
controller.commit_results = commit_results
raise SystemExit(controller.main())
