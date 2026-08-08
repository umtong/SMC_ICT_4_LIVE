#!/usr/bin/env python3
"""Select OOS-promoted components including optional v68 liquidation exhaustion."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from select_promoted_components import choose as choose_core


def choose(
    *,
    latest: dict,
    v59: dict,
    v62: dict,
    v68: dict,
) -> dict:
    result = choose_core(latest=latest, v59=v59, v62=v62)
    components = set(result.get("components", []))
    reasons = dict(result.get("reasons", {}))
    v68_pass = (
        v68.get("classification")
        == "V68_LIQUIDATION_EXHAUSTION_PASSED_DEV_OOS_AND_CONTINUOUS"
    )
    if v68_pass:
        components.add("v68")
        reasons["v68"] = "DEV_OOS_CONTINUOUS_PASS"
    result.update(
        {
            "schema": "candidate-05-promoted-component-selection-v2",
            "components": sorted(components),
            "reasons": reasons,
            "v68_promoted": v68_pass,
            "run_later_authoritative": bool(v68_pass),
        },
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--v59", type=Path, required=True)
    parser.add_argument("--v62", type=Path, required=True)
    parser.add_argument("--v68", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-output", type=Path, required=True)
    args = parser.parse_args()
    result = choose(
        latest=json.loads(args.latest.read_text()),
        v59=json.loads(args.v59.read_text()),
        v62=json.loads(args.v62.read_text()),
        v68=json.loads(args.v68.read_text()),
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    args.env_output.write_text(
        f"export CANDIDATE05_COMPONENTS={','.join(result['components'])}\n"
        + "".join(
            f"export {key}={value}\n"
            for key, value in result.get("environment", {}).items()
        ),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
