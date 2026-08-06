"""Run the strongest hourly SAC candidate once with post-close path diagnostics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_equilibrium_matrix import _base as _equilibrium_base
from run_equilibrium_matrix import _run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/hourly-sac-excursion-diagnostic"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    config = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    config["candidate_variant"] = "hourly_sac_excursion_diagnostic"
    config["variant_description"] = (
        "Unchanged strongest 60-minute SAC directional-defense candidate; records MFE/MAE only after Nautilus closes each position."
    )
    config["logic"].update(
        {
            "engine": "ROLLING_AUCTION_LIQUIDITY_RELAY",
            "enable_srr": False,
            "enable_sac": True,
            "auction_entry_window_minutes": 55,
            "auction_sweep_min_atr": 0.10,
            "sac_entry_confirmation": "DIRECTIONAL_BODY",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
            "cooldown_bars": 3,
            "ambiguous_cooldown_bars": 2,
        },
    )
    config_path = output / "hourly_sac_excursion_diagnostic.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record = _run(config_path, output / "hourly_sac_excursion_diagnostic", 0, candidate_dir, repository)
    summary = {
        "purpose": "separate direction failure from exit-management failure without changing any decision",
        "first_week_only": True,
        "long_evaluation_authorized": False,
        "record": record,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metrics = record.get("metrics", {})
    lines = [
        "# Candidate 06 hourly SAC excursion diagnostic",
        "",
        "No entry, stop, target, sizing, cost or execution assumption changed.",
        "",
        f"- return code: `{record.get('returncode')}`",
        f"- trades: `{metrics.get('trades')}`",
        f"- geometric daily NAV growth: `{metrics.get('geometric_daily_nav_growth')}`",
        f"- win rate: `{metrics.get('win_rate')}`",
        f"- errors: `{metrics.get('errors')}`",
        "",
        "Per-trade MFE/MAE fields are in `trades.json` and `trades.csv`.",
    ]
    (output / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if record.get("returncode") == 0 and record.get("metrics") else 1


if __name__ == "__main__":
    raise SystemExit(main())
