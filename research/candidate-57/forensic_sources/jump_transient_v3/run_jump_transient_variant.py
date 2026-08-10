"""Build one frozen Candidate 57 transient-protection config and run Nautilus."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True)
    parser.add_argument("--mode", default="transient_be")
    parser.add_argument("--arm", type=float, required=True)
    parser.add_argument("--escape", type=float, required=True)
    parser.add_argument("--start", default="2025-11-01")
    parser.add_argument("--end", default="2025-11-14")
    parser.add_argument("--stage", default="development")
    args = parser.parse_args()

    config = json.loads(Path("research/candidate-51/config.json").read_text())
    for key in (
        "sma_offset_low", "sma_offset_high", "sma_stop_min_fraction",
        "sma_stop_max_fraction", "sma_stop_atr_buffer",
    ):
        config["strategy"].pop(key, None)
    config["strategy"].update({
        "cooldown_minutes": 0,
        "max_hold_minutes": 240,
        "funding_flatten_minute": 60,
        "funding_blackout_before_minutes": -1,
        "funding_blackout_after_minutes": -1,
        "jump_timeframe_minutes": 240,
        "jump_threshold_sigma": 2.0,
        "jump_volatility_window": 18,
        "jump_min_absolute_return": 0.0,
        "jump_terminal_atr_period": 14,
        "jump_stop_atr_multiple": 1.0,
        "jump_min_stop_fraction": 0.0015,
        "jump_emergency_target_fraction": 0.20,
        "jump_stop_mode": "impulse",
        "jump_selection_mode": "source",
        "jump_min_residual_share": 0.50,
        "jump_min_residual_z": 0.75,
        "jump_confirmation_minutes": 0,
        "jump_confirmation_bucket_minutes": 5,
        "jump_protection_mode": args.mode,
        "jump_protection_activation_r": args.arm,
        "jump_protection_floor_r": 0.0,
        "jump_protection_trail_gap_r": 999.0,
        "jump_protection_escape_r": args.escape,
    })
    work = Path(".work")
    work.mkdir(exist_ok=True)
    token = (
        f"candidate57-jump-transient-v3-{args.variant}"
        if args.stage == "development"
        else f"candidate57-jump-transient-v3-{args.stage}-{args.variant}"
    )
    config_path = work / f"{token}.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    env = dict(os.environ)
    env["PYTHONPATH"] = "research/candidate-51"
    command = [
        sys.executable, "research/candidate-51/launch.py",
        "--config", str(config_path),
        "--start", args.start, "--end", args.end,
        "--cache", f".cache/{token}",
        "--output", f"artifacts/candidate-57/{token}",
        "--workspace", f".work/{token}",
    ]
    subprocess.run(command, check=True, env=env)


if __name__ == "__main__":
    main()
