#!/usr/bin/env python3
"""Apply the frozen V2 scenario logic to the uniform V3 execution dataset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from derive_nt_lvcfr_v2_signals import derive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    args = parser.parse_args()

    prepared = args.prepared_root.resolve()
    source = prepared / "signals-v1.json"
    output = prepared / "signals.json"
    manifest_path = args.output_manifest.resolve()
    signals = derive(
        source_signals=source,
        raw_root=prepared / "raw",
        output_signals=output,
        output_manifest=manifest_path,
        dealing_range_minutes=240,
        minimum_origin_alignment=2.0 / 3.0,
        minimum_acceptance_fraction=0.5,
    )

    rewritten = []
    for signal in signals:
        item = dict(signal)
        item["scenario_id"] = str(item["scenario_id"]).replace(
            "NT-LVCFR-V2-",
            "NT-LVCFR-V3-",
        )
        rewritten.append(item)
    output.write_text(
        json.dumps(rewritten, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    schedule_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schedule_manifest["candidate"] = "candidate-03-nt-lvcfr-v3"
    schedule_manifest["output_signals"] = str(output)
    manifest_path.write_text(
        json.dumps(schedule_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    data_manifest_path = prepared / "data_manifest.json"
    data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
    data_manifest["candidate"] = "candidate-03-nt-lvcfr-v3"
    data_manifest["signals"] = len(rewritten)
    data_manifest["signal_path"] = output.as_posix()
    data_manifest["scenario_transform"] = {
        "dealing_range_minutes": 240,
        "directional_origin_outer_fraction": 1.0 / 3.0,
        "acceptance_minutes": 1,
        "minimum_directional_event_range_fraction": 0.5,
        "source": "frozen V2 premium-discount and acceptance logic",
    }
    data_manifest_path.write_text(
        json.dumps(data_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "candidate": "candidate-03-nt-lvcfr-v3",
                "source_signals": schedule_manifest["source_signal_count"],
                "derived_signals": len(rewritten),
                "rejected_by_origin_location": schedule_manifest[
                    "rejected_by_origin_location"
                ],
                "rejected_by_one_minute_acceptance": schedule_manifest[
                    "rejected_by_one_minute_acceptance"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
