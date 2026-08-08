#!/usr/bin/env python3
"""Assemble verified four-symbol chunks and run one unbroken Nautilus account."""
from __future__ import annotations

import argparse
from datetime import date
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"
for path in (CANDIDATE05, CANDIDATE16, HERE):
    text = str(path)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

strategy_module = importlib.import_module("strategy")
if Path(strategy_module.__file__).resolve() != (HERE / "strategy.py").resolve():
    raise RuntimeError(f"Candidate 35 strategy collision: {strategy_module.__file__}")

from chunk_assembly import SYMBOLS, assemble_universe, sha256_file

spec = importlib.util.spec_from_file_location("candidate35_direct_runner", HERE / "run.py")
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load Candidate 35 direct runner")
direct = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = direct
spec.loader.exec_module(direct)


def run_continuous(
    *,
    input_root: Path,
    config_path: Path,
    start: date,
    end: date,
    cache: Path,
    workspace: Path,
    output: Path,
) -> dict[str, Any]:
    output = output.resolve()
    workspace = workspace.resolve()
    cache = cache.resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    frames, feature_paths, continuous_manifest = assemble_universe(
        input_root=input_root.resolve(),
        start=start,
        end=end,
        workspace=workspace / "assembled",
        symbols=SYMBOLS,
    )
    continuous_manifest_path = output / "continuous_input_manifest.json"
    continuous_manifest_path.write_text(
        json.dumps(continuous_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    def prebuilt_load_inputs(
        *, start: date, end: date, cache: Path, output: Path
    ) -> tuple[dict[str, Any], dict[str, Path], dict[str, Any]]:
        del cache, output
        if start.isoformat() != continuous_manifest["start"] or end.isoformat() != continuous_manifest["end"]:
            raise RuntimeError(f"continuous range changed: {start} through {end}")
        records = {
            symbol: {
                "mode": "checksum-verified-chunk-assembly",
                **continuous_manifest["symbols"][symbol],
            }
            for symbol in SYMBOLS
        }
        return frames, feature_paths, records

    direct.load_inputs = prebuilt_load_inputs
    metrics = direct.run(
        config_path=config_path.resolve(),
        start=start,
        end=end,
        cache=cache,
        output=output,
        workspace=workspace,
    )
    metrics.update(
        {
            "validation_mode": "single-unbroken-checksum-verified-chunk-replay",
            "continuous_account": True,
            "continuous_strategy_process": True,
            "account_restarts": 0,
            "strategy_restarts": 0,
            "input_chunk_count": sum(
                int(item["chunk_count"])
                for item in continuous_manifest["symbols"].values()
            ),
            "continuous_input_manifest_sha256": sha256_file(continuous_manifest_path),
        },
    )
    metrics["gate_checks"]["continuous_account"] = True
    metrics["gate_checks"]["continuous_strategy_process"] = True
    metrics["gate_pass"] = all(metrics["gate_checks"].values())
    (output / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    run_path = output / "run.json"
    if run_path.is_file():
        run_manifest = json.loads(run_path.read_text(encoding="utf-8"))
        run_manifest.update(
            {
                "validation_mode": metrics["validation_mode"],
                "continuous_input_manifest_sha256": metrics["continuous_input_manifest_sha256"],
                "input_chunk_count": metrics["input_chunk_count"],
                "account_restarts": 0,
                "strategy_restarts": 0,
            },
        )
        run_path.write_text(
            json.dumps(run_manifest, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metrics = run_continuous(
        input_root=args.input_root,
        config_path=args.config,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache=args.cache,
        workspace=args.workspace,
        output=args.output,
    )
    print(json.dumps(metrics, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
