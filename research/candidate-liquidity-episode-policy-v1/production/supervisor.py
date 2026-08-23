from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

from .config import ProductionConfig


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_supervisor(config_path: str | Path, *, duration_seconds: float | None = None) -> int:
    config_path = Path(config_path).resolve()
    config = ProductionConfig.load(config_path)
    if config.mode.value not in {"paper", "testnet"}:
        raise ValueError("supervisor requires paper or testnet configuration")
    commands = {
        "producer": [sys.executable, "-m", "production.cli", "producer", "--config", str(config_path)],
        "nautilus": [sys.executable, "-m", "production.cli", "nautilus-node", "--config", str(config_path)],
    }
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    processes: dict[str, subprocess.Popen[Any]] = {}
    started = datetime.now(timezone.utc).isoformat()
    try:
        # Execution starts first and reconciles its empty/sandbox account before a
        # producer may publish an executable decision.
        for name in ("nautilus", "producer"):
            processes[name] = subprocess.Popen(commands[name], creationflags=creationflags)
            time.sleep(2.0 if name == "nautilus" else 0.2)
        start = time.monotonic()
        while True:
            for name, process in processes.items():
                code = process.poll()
                if code is not None:
                    raise RuntimeError(f"{name} process exited unexpectedly with code {code}")
            if duration_seconds is not None and time.monotonic() - start >= duration_seconds:
                break
            time.sleep(1.0)
        return 0
    finally:
        for process in processes.values():
            if process.poll() is None:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.send_signal(signal.SIGINT)
        deadline = time.monotonic() + 20.0
        for process in processes.values():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.terminate()
        for process in processes.values():
            if process.poll() is None:
                process.kill()
        evidence = {
            "started_at_utc": started,
            "stopped_at_utc": datetime.now(timezone.utc).isoformat(),
            "config": config.to_dict(),
            "children": {
                name: {"pid": process.pid, "returncode": process.poll(), "command": commands[name]}
                for name, process in processes.items()
            },
        }
        _atomic_json(config.evidence_dir / "supervisor.json", evidence)
