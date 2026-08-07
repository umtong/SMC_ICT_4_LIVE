#!/usr/bin/env python3
"""Run frozen V17 staged NautilusTrader evidence inside the pinned job image.

This controller intentionally uses only /tmp for mutable work, verifies frozen
Git blob identities, runs week two, then either week three or the single
permitted expansion ablation, and commits all native evidence atomically through
the GitHub Git Data API. It does not calculate fills, PnL, or NAV itself.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
BRANCH = "research/candidate-03"
WORK_ROOT = Path("/tmp/candidate03-v17-staged")
REPO_ROOT = WORK_ROOT / "repo"
CONFIG = Path("research/candidate-03/nt_lvcfr_v17_config.json")
FROZEN_BLOBS = {
    "research/candidate-03/derive_nt_lvcfr_v17_signals.py": "fcc05dd19bbfc621226250743979d341a7194bf7",
    "research/candidate-03/nt_lvcfr_v17_config.json": "64c7ef99cc076582ffff59c961208bc09d22cae7",
    "research/candidate-03/nt_lvcfr_strategy.py": "e4d00ae0c6fa1d24198c846bccb247baacdc0456",
    "research/candidate-03/run_nt_lvcfr.py": "74bb02f1b69ee31ce32ddfa47497bdd9770ac00b",
}


def github_api(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v17-staged-container",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload_bytes = response.read()
    return json.loads(payload_bytes.decode()) if payload_bytes else None


def fetch_branch() -> None:
    shutil.rmtree(WORK_ROOT, ignore_errors=True)
    WORK_ROOT.mkdir(parents=True)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/tarball/{BRANCH}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "candidate-03-v17-staged-container",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        archive_bytes = response.read()
    extract_root = WORK_ROOT / "extract"
    extract_root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        members = archive.getmembers()
        roots = {Path(member.name).parts[0] for member in members if Path(member.name).parts}
        if len(roots) != 1:
            raise RuntimeError(f"unexpected archive roots: {roots}")
        for member in members:
            target = Path(member.name)
            if target.is_absolute() or ".." in target.parts:
                raise RuntimeError(f"unsafe archive member: {member.name}")
        archive.extractall(extract_root, filter="data")
    shutil.move(str(extract_root / next(iter(roots))), REPO_ROOT)


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def verify_frozen_sources() -> None:
    for relative, expected in FROZEN_BLOBS.items():
        actual = git_blob_sha(REPO_ROOT / relative)
        if actual != expected:
            raise RuntimeError(f"frozen source mismatch: {relative}: {actual} != {expected}")


def environment() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{REPO_ROOT / 'research/candidate-03'}"
    return env


def run(command: list[str], *, log_path: Path | None = None, check: bool = True) -> int:
    print("+", " ".join(command), flush=True)
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    return_code = process.wait()
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("".join(lines), encoding="utf-8")
    if check and return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)
    return return_code


def verify_runtime() -> None:
    run(["smc4", "doctor"])
    tests = [
        "test_nt_lvcfr.py",
        "test_nt_lvcfr_trade_proxy.py",
        "test_nt_lvcfr_v13.py",
        "test_nt_lvcfr_v17.py",
        "test_nt_lvcfr_v17_ablation.py",
    ]
    for name in tests:
        run([sys.executable, f"research/candidate-03/{name}"])


def run_week(week: str, prepared: Path, output: Path) -> int:
    prepared.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            "research/candidate-03/prepare_nt_lvcfr_trade_proxy.py",
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
    run(
        [
            sys.executable,
            "research/candidate-03/derive_nt_lvcfr_v17_signals.py",
            "--prepared-root",
            str(prepared),
            "--output-manifest",
            str(output / "v17_signal_manifest.json"),
        ]
    )
    shutil.copy2(prepared / "signals.json", output / "signals.json")
    run(
        [
            sys.executable,
            "research/candidate-03/rebuild_nt_lvcfr_trade_proxy_catalog.py",
            "--prepared-root",
            str(prepared),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "catalog_rebuild.log",
    )
    run(
        [
            sys.executable,
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
    gate_status = run(
        [
            sys.executable,
            "research/candidate-03/gate_nt_lvcfr.py",
            str(output / "metrics.json"),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "gate.json",
        check=False,
    )
    (output / "gate_status.txt").write_text(f"{gate_status}\n", encoding="utf-8")
    run(
        [
            sys.executable,
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


def run_expansion_ablation(prepared: Path, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(prepared / "signals.json", prepared / "signals-v17-full.json")
    run(
        [
            sys.executable,
            "research/candidate-03/ablate_nt_lvcfr_v17_expansion.py",
            "--prepared-root",
            str(prepared),
            "--output-manifest",
            str(output / "ablation_manifest.json"),
        ]
    )
    shutil.copy2(prepared / "signals.json", output / "signals.json")
    run(
        [
            sys.executable,
            "research/candidate-03/rebuild_nt_lvcfr_trade_proxy_catalog.py",
            "--prepared-root",
            str(prepared),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "catalog_rebuild.log",
    )
    run(
        [
            sys.executable,
            "research/candidate-03/run_nt_lvcfr.py",
            "--week-start",
            "2025-06-23",
            "--prepared-root",
            str(prepared),
            "--output",
            str(output),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "backtest.log",
    )
    status = run(
        [
            sys.executable,
            "research/candidate-03/gate_nt_lvcfr.py",
            str(output / "metrics.json"),
            "--config",
            str(CONFIG),
        ],
        log_path=output / "gate.json",
        check=False,
    )
    (output / "gate_status.txt").write_text(f"{status}\n", encoding="utf-8")
    run(
        [
            sys.executable,
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


def copy_results(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    allowed = {".json", ".jsonl", ".csv", ".log", ".txt"}
    for path in source.iterdir():
        if path.is_file() and path.suffix in allowed:
            shutil.copy2(path, destination / path.name)


def commit_results(next_stage: str) -> str:
    result_root = REPO_ROOT / "research/candidate-03/results"
    week2_gate = json.loads((result_root / "v17-development-2/gate.json").read_text())
    status: dict[str, Any] = {
        "candidate": "candidate-03-nt-lvcfr-v17-dual-inventory-auction",
        "week2_passed": bool(week2_gate["passed"]),
        "next_stage": next_stage,
        "frozen_source_blobs": FROZEN_BLOBS,
    }
    week3_gate = result_root / "v17-validation-3/gate.json"
    if week3_gate.exists():
        status["week3_passed"] = bool(json.loads(week3_gate.read_text())["passed"])
    ablation_gate = result_root / "v17-expansion-ablation-development-2/gate.json"
    if ablation_gate.exists():
        status["expansion_ablation_passed"] = bool(json.loads(ablation_gate.read_text())["passed"])
    (result_root / "V17_STAGED_STATUS.json").write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    ref = github_api("GET", f"/git/ref/heads/{BRANCH}")
    parent = ref["object"]["sha"]
    parent_commit = github_api("GET", f"/git/commits/{parent}")
    entries: list[dict[str, str]] = []
    for path in sorted(item for item in result_root.rglob("*") if item.is_file()):
        blob = github_api(
            "POST",
            "/git/blobs",
            {
                "content": base64.b64encode(path.read_bytes()).decode(),
                "encoding": "base64",
            },
        )
        entries.append(
            {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )
    tree = github_api(
        "POST",
        "/git/trees",
        {"base_tree": parent_commit["tree"]["sha"], "tree": entries},
    )
    commit = github_api(
        "POST",
        "/git/commits",
        {
            "message": "candidate-03: record staged V17 native evidence",
            "tree": tree["sha"],
            "parents": [parent],
        },
    )
    github_api(
        "PATCH",
        f"/git/refs/heads/{BRANCH}",
        {"sha": commit["sha"], "force": False},
    )
    print(json.dumps({"committed_sha": commit["sha"], **status}, indent=2, sort_keys=True))
    return commit["sha"]


def main() -> int:
    fetch_branch()
    verify_frozen_sources()
    verify_runtime()

    stage_root = REPO_ROOT / "artifacts/candidate-03/v17-staged"
    week2_prepared = stage_root / "development-2/prepared"
    week2_output = stage_root / "development-2/output"
    week2_status = run_week("2025-06-23", week2_prepared, week2_output)

    if week2_status == 0:
        next_stage = "validation-3"
        week3_output = stage_root / "validation-3/output"
        final_status = run_week(
            "2022-05-16",
            stage_root / "validation-3/prepared",
            week3_output,
        )
    else:
        next_stage = "expansion-ablation"
        ablation_output = stage_root / "expansion-ablation-development-2/output"
        run_expansion_ablation(week2_prepared, ablation_output)
        final_status = 1

    results = REPO_ROOT / "research/candidate-03/results"
    copy_results(week2_output, results / "v17-development-2")
    if (stage_root / "validation-3/output").exists():
        copy_results(stage_root / "validation-3/output", results / "v17-validation-3")
    if (stage_root / "expansion-ablation-development-2/output").exists():
        copy_results(
            stage_root / "expansion-ablation-development-2/output",
            results / "v17-expansion-ablation-development-2",
        )
    commit_results(next_stage)
    return final_status


if __name__ == "__main__":
    raise SystemExit(main())
