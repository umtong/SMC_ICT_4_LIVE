#!/usr/bin/env python3
"""Run V28's single permitted local-analog model ablation."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import types
import urllib.request
from pathlib import Path

TOKEN = os.environ["GITHUB_TOKEN"]
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
REF = os.environ.get("GITHUB_SHA", "research/candidate-03")
CONTROLLER = "research/candidate-03/run_v28_staged_container.py"


def fetch_raw(path: str) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{path}?ref={REF}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v28-analog",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8")


def load_controller() -> types.ModuleType:
    source = fetch_raw(CONTROLLER)
    terminal = "raise SystemExit(main())"
    if terminal not in source:
        raise RuntimeError("V28 terminal call not found")
    module = types.ModuleType("candidate03_v28_controller")
    module.__file__ = CONTROLLER
    exec(compile(source.rsplit(terminal, 1)[0], CONTROLLER, "exec"), module.__dict__)
    return module


controller = load_controller()
base = controller.base


def verify_runtime() -> str:
    base.run(["smc4", "doctor"])
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/apply_nt_lvcfr_cost_viability_patch.py",
            "research/candidate-03/nt_lvcfr_strategy.py",
        ]
    )
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/patch_v27_parser_v2.py",
            "research/candidate-03/derive_nt_lvcfr_v27_signals.py",
        ]
    )
    for test in (
        "test_nt_lvcfr.py",
        "test_nt_lvcfr_trade_proxy.py",
        "test_nt_lvcfr_cost_viability.py",
        "test_nt_lvcfr_v28.py",
        "test_nt_lvcfr_v28_analog.py",
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
            "--week-start",
            week,
            "--output",
            str(prepared),
            "--config",
            str(controller.CONFIG),
        ],
        log_path=output / "preparation.log",
    )
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/derive_nt_lvcfr_v28_analog_signals.py",
            "--week-start",
            week,
            "--prepared-root",
            str(prepared),
            "--output-manifest",
            str(output / "v28_signal_manifest.json"),
        ],
        log_path=output / "v28_analog_derivation.log",
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
    manifest = json.loads((output / "v28_signal_manifest.json").read_text(encoding="utf-8"))
    preflight = {
        "candidate": "candidate-03-nt-lvcfr-v28-analog-ablation",
        "week_start": week,
        "training_start": manifest["training_start"],
        "training_end": manifest["training_end"],
        "training_events": manifest["training_events"],
        "training_label_counts": manifest["training_label_counts"],
        "evaluation_events": manifest["evaluation_events"],
        "v28_signal_count": len(signals),
        "state_counts": manifest["state_counts"],
        "no_trade_reasons": manifest["no_trade_reasons"],
        "continuation_model": manifest["continuation_model"],
        "reversal_model": manifest["reversal_model"],
        "minimum_required_episodes": 8,
        "opportunity_gate_reachable": len(signals) >= 8,
        "performance_metrics_calculated": False,
        "ablation": manifest["ablation"],
    }
    (output / "preflight_summary.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(preflight, indent=2, sort_keys=True))
    return len(signals)


controller.verify_runtime = verify_runtime
controller.prepare_schedule = prepare_schedule
# This is already V28's single core-variable ablation. A second state removal is
# deliberately disabled even if the native week fails.
controller.worst_negative_state = lambda output: None
raise SystemExit(controller.main())
