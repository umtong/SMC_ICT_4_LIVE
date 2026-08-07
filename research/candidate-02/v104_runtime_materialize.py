#!/usr/bin/env python3
"""Verify and materialize the prospectively frozen candidate-02 v104 source.

This tool changes only pre-performance infrastructure contracts discovered
before features/signals/NautilusTrader results: the common image test command
and the copied v75 archive-cardinality assertion. It does not alter scenario,
week, parameters, risk, costs, target selection, or the fixed ablation.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path.cwd()
PAYLOAD_ROOT = ROOT / ".candidate-02-v104"
MANIFEST_PATH = PAYLOAD_ROOT / "payload_manifest.json"
CANDIDATE = ROOT / "research/candidate-02"
DRIVER_PATH = CANDIDATE / "v104_first_week_driver.py"
LOCK_PATH = CANDIDATE / "v104_external_liquidity_lock.json"
WORKFLOW_PATH = ROOT / ".github/workflows/candidate-02-v77-terminal-router.yml"
THIS_PATH = CANDIDATE / "v104_runtime_materialize.py"


def git_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def verify_and_extract_payload() -> dict[str, object]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if int(manifest["parts"]) != 5:
        raise RuntimeError("unexpected v104 payload part count")
    first_chunks = [PAYLOAD_ROOT / f"payload.part00.{index:03d}" for index in range(4)]
    later_parts = [PAYLOAD_ROOT / f"payload.part{index:02d}" for index in range(1, 5)]
    if not all(path.is_file() for path in first_chunks + later_parts):
        raise FileNotFoundError("incomplete v104 payload")
    first = b"".join(path.read_bytes() for path in first_chunks)
    if len(first) != 8000:
        raise RuntimeError("v104 first payload part size mismatch")
    first_blob = hashlib.sha1(b"blob " + str(len(first)).encode() + b"\0" + first).hexdigest()
    if first_blob != "68ba636fe7af708325e3c3a57d5119097a0129b6":
        raise RuntimeError("v104 reconstructed first payload Git blob mismatch")
    encoded = first + b"".join(path.read_bytes() for path in later_parts)
    if len(encoded) != int(manifest["base64_chars"]):
        raise RuntimeError("v104 payload base64 size mismatch")
    if hashlib.sha256(encoded).hexdigest() != manifest["base64_sha256"]:
        raise RuntimeError("v104 payload base64 SHA-256 mismatch")
    archive = base64.b64decode(encoded, validate=True)
    if len(archive) != int(manifest["tar_gz_bytes"]):
        raise RuntimeError("v104 source archive size mismatch")
    if hashlib.sha256(archive).hexdigest() != manifest["tar_gz_sha256"]:
        raise RuntimeError("v104 source archive SHA-256 mismatch")
    archive_path = Path("/tmp/v104-source.tar.gz")
    archive_path.write_bytes(archive)
    expected_members = sorted(str(value) for value in manifest["files"])
    with tarfile.open(archive_path, "r:gz") as stream:
        actual_members = sorted(member.name for member in stream.getmembers() if member.isfile())
        if actual_members != expected_members:
            raise RuntimeError(f"v104 archive members mismatch: {actual_members}")
        stream.extractall(ROOT, filter="data")
    for relative, expected in dict(manifest["files"]).items():
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size != int(expected["bytes"]):
            raise RuntimeError(f"v104 extracted source size mismatch: {relative}")
        actual = git_blob(path)
        if actual != expected["git_blob_sha"]:
            raise RuntimeError(f"v104 extracted Git blob mismatch: {relative}")
    return manifest


def patch_driver() -> None:
    text = DRIVER_PATH.read_text(encoding="utf-8")
    before_test = (
        '    run([sys.executable, "-m", "pytest", "-q", '
        'str(CANDIDATE / "tests/test_v104_causality.py"), '
        'str(CANDIDATE / "tests/test_v104_activation_adapter.py")])'
    )
    after_test = '    run([sys.executable, str(CANDIDATE / "tests/run_v104_tests.py")])'
    if text.count(before_test) != 1:
        raise RuntimeError("v104 driver test-command materialization mismatch")
    text = text.replace(before_test, after_test)

    before_warmup = '    warmup_days = int(base["validation"]["warmup_days"])\n'
    after_warmup = before_warmup + '    expected_archives = warmup_days + 8\n'
    if text.count(before_warmup) != 1:
        raise RuntimeError("v104 driver warmup materialization mismatch")
    text = text.replace(before_warmup, after_warmup)

    before_loop_write = (
        '            warmup_changes += count\n'
        '        path.write_text(text, encoding="utf-8")\n'
    )
    after_loop_write = '''            warmup_changes += count
        if path.name == "build_features.py":
            archive_before = (
                '    if len(agg_frames) != 10 or len(book_frames) != 10:\\n'
                '        raise ValueError("expected ten daily direct-data archives per source")'
            )
            archive_after = (
                f'    if len(agg_frames) != {expected_archives} or len(book_frames) != {expected_archives}:\\n'
                f'        raise ValueError("expected {expected_archives} daily direct-data archives per source")'
            )
            if text.count(archive_before) != 1:
                raise RuntimeError("v75 feature-builder archive assertion materialization mismatch")
            text = text.replace(archive_before, archive_after)
        path.write_text(text, encoding="utf-8")
'''
    if text.count(before_loop_write) != 1:
        raise RuntimeError("v104 driver feature-builder insertion mismatch")
    text = text.replace(before_loop_write, after_loop_write)
    DRIVER_PATH.write_text(text, encoding="utf-8")


def update_lock(manifest: dict[str, object]) -> None:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if lock["status"] != "LOCKED_BEFORE_FIRST_WEEK_COLLECTION":
        raise RuntimeError("v104 lock status changed")
    if lock["first_week"]["raw_data_status_at_lock"] != "NOT_COLLECTED_FOR_V104":
        raise RuntimeError("v104 original predata status changed")
    if lock["source_git_blob_sha"]["router_workflow"] != "9c4289b41a16f39c5ac04a1471167d53f6276c72":
        raise RuntimeError("v104 original router placeholder changed")
    if lock["source_git_blob_sha"]["driver"] != "b9e0fb9da71ebd5f151fd459c750fb3301d605dd":
        raise RuntimeError("v104 original driver blob changed")

    lock["source_git_blob_sha"]["router_workflow"] = git_blob(WORKFLOW_PATH)
    lock["source_git_blob_sha"]["driver"] = git_blob(DRIVER_PATH)
    additions = {
        "controlled_test_runner": CANDIDATE / "tests/run_v104_tests.py",
        "schedule_contract_test": CANDIDATE / "tests/test_v104_schedule_contract.py",
        "runtime_materializer": THIS_PATH,
    }
    for key, path in additions.items():
        if not path.is_file():
            raise FileNotFoundError(path)
        lock["source_files"][key] = str(path.relative_to(ROOT))
        lock["source_git_blob_sha"][key] = git_blob(path)
    lock["source_materialization"] = {
        "archive_count_patch_before_features": True,
        "contract": (
            "payload bytes, archive, extracted sizes, every extracted Git blob, "
            "patched driver, router, runtime materializer, and controlled tests "
            "are verified before features, signals, or performance"
        ),
        "driver_test_command_patch_before_feature_build": True,
        "materialization_commit_does_not_retrigger_router": True,
        "materialized_before_features_signals_or_performance": True,
        "payload_manifest": str(MANIFEST_PATH.relative_to(ROOT)),
        "payload_parts": int(manifest["parts"]),
    }
    LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    manifest = verify_and_extract_payload()
    patch_driver()
    update_lock(manifest)
    print(
        json.dumps(
            {
                "driver_git_blob": git_blob(DRIVER_PATH),
                "lock_git_blob": git_blob(LOCK_PATH),
                "runtime_materializer_git_blob": git_blob(THIS_PATH),
                "router_git_blob": git_blob(WORKFLOW_PATH),
                "status": "V104_SOURCE_MATERIALIZED_BEFORE_FEATURES_SIGNALS_OR_PERFORMANCE",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
