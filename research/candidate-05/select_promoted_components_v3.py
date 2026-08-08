#!/usr/bin/env python3
"""Select OOS-promoted components including v68 and optional v70."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from select_promoted_components_v2 import choose as choose_v2


def choose(
    *,
    latest: dict,
    v59: dict,
    v62: dict,
    v68: dict,
    v70: dict,
) -> dict:
    result = choose_v2(latest=latest, v59=v59, v62=v62, v68=v68)
    components = set(result.get("components", []))
    reasons = dict(result.get("reasons", {}))
    v70_pass = (
        v70.get("classification")
        == "V70_PARTICIPATION_EXPANSION_PASSED_DEV_OOS_AND_CONTINUOUS"
    )
    if v70_pass:
        components.add("v70")
        reasons["v70"] = "DEV_OOS_CONTINUOUS_PASS"
    result.update(
        {
            "schema": "candidate-05-promoted-component-selection-v3",
            "components": sorted(components),
            "reasons": reasons,
            "v70_promoted": v70_pass,
            "run_later_participation_authoritative": bool(v70_pass),
        },
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--v59", type=Path, required=True)
    parser.add_argument("--v62", type=Path, required=True)
    parser.add_argument("--v68", type=Path, required=True)
    parser.add_argument("--v70", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-output", type=Path, required=True)
    args = parser.parse_args()
    result = choose(
        latest=json.loads(args.latest.read_text()),
        v59=json.loads(args.v59.read_text()),
        v62=json.loads(args.v62.read_text()),
        v68=json.loads(args.v68.read_text()),
        v70=json.loads(args.v70.read_text()),
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
