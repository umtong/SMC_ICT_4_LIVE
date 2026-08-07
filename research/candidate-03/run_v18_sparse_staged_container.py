#!/usr/bin/env python3
"""Execute the frozen V18 staged controller with sparse official-data prep."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import urllib.request
from pathlib import Path

TOKEN = os.environ["GITHUB_TOKEN"]
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
REF = os.environ.get("GITHUB_SHA", "research/candidate-03")
BASE_PATH = "research/candidate-03/run_v18_staged_container.py"


def load_base():
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{BASE_PATH}?ref={REF}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v18-sparse-staged-container",
        },
    )
    target = Path("/tmp/run_v18_staged_container_base.py")
    with urllib.request.urlopen(request, timeout=120) as response:
        target.write_bytes(response.read())
    spec = importlib.util.spec_from_file_location("v18_base", target)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load V18 staged controller")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base()
original_verify_runtime = base.verify_runtime


def verify_runtime() -> None:
    original_verify_runtime()
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/test_prepare_nt_lvcfr_v18_sparse.py",
        ]
    )


def prepare_schedule(week: str, prepared: Path, output: Path) -> int:
    prepared.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/prepare_nt_lvcfr_v18_sparse.py",
            "--week-start",
            week,
            "--output",
            str(prepared),
            "--config",
            str(base.CONFIG),
        ],
        log_path=output / "preparation.log",
    )
    shutil.copy2(prepared / "signals.json", prepared / "signals-v1.json")
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/derive_nt_lvcfr_v18_signals.py",
            "--prepared-root",
            str(prepared),
            "--output-manifest",
            str(output / "v18_signal_manifest.json"),
        ],
        log_path=output / "derivation.log",
    )
    shutil.copy2(prepared / "signals.json", output / "signals.json")
    shutil.copy2(prepared / "data_manifest.json", output / "data_manifest.json")
    signals = json.loads((prepared / "signals.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "v18_signal_manifest.json").read_text(encoding="utf-8"))
    preflight = {
        "candidate": manifest["candidate"],
        "week_start": week,
        "engine_status": "causal_opportunity_preflight_only_no_backtest",
        "derived_signal_count": len(signals),
        "minimum_required_episodes": 8,
        "opportunity_gate_reachable": len(signals) >= 8,
        "state_counts": manifest["state_counts"],
        "l1_routing_counts": manifest["l1_routing_counts"],
        "threshold_policy": manifest["threshold_policy"],
        "performance_metrics_calculated": False,
        "data_acquisition": json.loads(
            (prepared / "data_manifest.json").read_text(encoding="utf-8")
        ).get("data_acquisition"),
    }
    (output / "preflight_summary.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(preflight, indent=2, sort_keys=True))
    return len(signals)


base.verify_runtime = verify_runtime
base.prepare_schedule = prepare_schedule
raise SystemExit(base.main())
