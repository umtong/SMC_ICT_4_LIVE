from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import zipfile


class Candidate02V103PRPipelineTest(unittest.TestCase):
    """Execute the prospectively locked v103 research path in default-branch CI.

    This file exists only on the temporary unmerged trigger branch.  A rejected
    strategy is a valid test outcome; only implementation or evidence failures
    fail CI.  All performance remains owned by NautilusTrader.
    """

    def test_locked_first_week_produces_machine_decision(self) -> None:
        root = Path(__file__).resolve().parents[1]
        evidence_root = root / "artifacts/ci-smoke/v103"
        evidence_root.mkdir(parents=True, exist_ok=True)
        stdout_path = evidence_root / "driver.log"
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [
                str(root / "src"),
                str(root / "research/candidate-02"),
                env.get("PYTHONPATH", ""),
            ]
        )
        env["SMC4_PREBUILT_ENV"] = "1"
        env["GITHUB_STEP_SUMMARY"] = str(evidence_root / "step-summary.md")

        result = subprocess.run(
            [sys.executable, "research/candidate-02/v103_first_week_driver.py"],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=1_500,
        )
        stdout_path.write_text(result.stdout or "", encoding="utf-8")
        if result.returncode != 0:
            self.fail(
                "v103 driver implementation failed; tail follows:\n"
                + (result.stdout or "")[-20_000:]
            )

        final_path = root / "artifacts-v103-first-week-decision.json"
        baseline_path = root / "artifacts-v103-baseline-decision.json"
        manifest_path = root / "artifacts-v103-variant-manifest.json"
        for path in (final_path, baseline_path, manifest_path):
            self.assertTrue(path.exists(), f"missing v103 evidence: {path}")
            shutil.copy2(path, evidence_root / path.name)

        decision = json.loads(final_path.read_text(encoding="utf-8"))
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        self.assertEqual(
            decision["candidate_family"],
            "candidate-02-v103-endogenous-turnover-clock-order-flow-regimes",
        )
        self.assertEqual(baseline["performance_engine"], "NautilusTrader 1.230.0")
        self.assertFalse(baseline["custom_backtest_engine"])
        self.assertAlmostEqual(float(baseline["risk_fraction"]), 0.03)

        compact = evidence_root / "compact-evidence.zip"
        with zipfile.ZipFile(compact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in (final_path, baseline_path, manifest_path, stdout_path):
                archive.write(path, arcname=path.name)
            for output in sorted((root / "artifacts").glob("candidate-02-v103-*")):
                if not output.is_dir():
                    continue
                for name in (
                    "metrics.json",
                    "trades.jsonl",
                    "signals.jsonl",
                    "risk_sizing.jsonl",
                    "orders.csv",
                    "order_fills.csv",
                    "positions.csv",
                    "account.csv",
                    "validation_exit_code.txt",
                ):
                    path = output / name
                    if path.exists():
                        archive.write(path, arcname=f"{output.name}/{name}")


if __name__ == "__main__":
    unittest.main()
