#!/usr/bin/env python3
"""Run V31 with deterministic NumPy nonlinear features in the pinned image."""
from __future__ import annotations

import hashlib
import os
import types
import urllib.request

TOKEN = os.environ["GITHUB_TOKEN"]
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
REF = os.environ.get("GITHUB_SHA", "research/candidate-03")
CONTROLLER = "research/candidate-03/run_v31_staged_container.py"


def fetch_raw(path: str) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{path}?ref={REF}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v31-v2",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8")


def load_controller() -> types.ModuleType:
    source = fetch_raw(CONTROLLER)
    terminal = "raise SystemExit(controller.main())"
    if terminal not in source:
        raise RuntimeError("V31 terminal call not found")
    module = types.ModuleType("candidate03_v31_controller")
    module.__file__ = CONTROLLER
    exec(compile(source.rsplit(terminal, 1)[0], CONTROLLER, "exec"), module.__dict__)
    return module


wrapper = load_controller()
controller = wrapper.controller
base = wrapper.base


def verify_runtime() -> str:
    base.run(["smc4", "doctor"])
    base.run(
        [
            base.sys.executable,
            "research/candidate-03/apply_nt_lvcfr_cost_viability_patch.py",
            "research/candidate-03/nt_lvcfr_strategy.py",
        ]
    )
    detector = "research/candidate-03/derive_nt_lvcfr_v31_signals.py"
    for patch in (
        "patch_v31_causal_volatility.py",
        "patch_v31_numpy_model.py",
    ):
        base.run(
            [
                base.sys.executable,
                f"research/candidate-03/{patch}",
                detector,
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


controller.verify_runtime = verify_runtime
raise SystemExit(controller.main())
