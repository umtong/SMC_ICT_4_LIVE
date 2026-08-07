#!/usr/bin/env python3
"""Compatibility wrapper for the V26 staged controller.

The original V26 controller inherited V24's process-relative strategy hash.
GitHub Actions runs the controller from /tmp while the writable branch lives at
base.REPO_ROOT. This wrapper changes only that path resolution and reruns the
same frozen V26 logic and weeks.
"""
from __future__ import annotations

import hashlib
import os
import types
import urllib.request

TOKEN = os.environ["GITHUB_TOKEN"]
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
REF = os.environ.get("GITHUB_SHA", "research/candidate-03")
CONTROLLER = "research/candidate-03/run_v26_staged_container.py"


def fetch_raw(path: str) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/contents/{path}?ref={REF}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v26-staged-container-v2",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8")


def load_controller() -> types.ModuleType:
    source = fetch_raw(CONTROLLER)
    terminal = "raise SystemExit(main())"
    if terminal not in source:
        raise RuntimeError("V26 terminal call not found")
    module = types.ModuleType("candidate03_v26_controller")
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
    for test in (
        "test_nt_lvcfr.py",
        "test_nt_lvcfr_trade_proxy.py",
        "test_nt_lvcfr_cost_viability.py",
        "test_nt_lvcfr_v24.py",
        "test_nt_lvcfr_v26.py",
    ):
        base.run([base.sys.executable, f"research/candidate-03/{test}"])
    return hashlib.sha256(
        (base.REPO_ROOT / "research/candidate-03/nt_lvcfr_strategy.py").read_bytes()
    ).hexdigest()


controller.verify_runtime = verify_runtime
raise SystemExit(controller.main())
