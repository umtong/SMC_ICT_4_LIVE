"""Dispatch one already-qualified shared acceptance runner without changing its contract."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
IMPLEMENTATION_V3 = HERE / "evidence" / "shared-acceptance-implementation-v3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument(
        "--runner-kind",
        required=True,
        choices=("BASE", "BASE_ABLATION", "IMPLEMENTATION", "IMPLEMENTATION_ABLATION"),
    )
    args, remaining = parser.parse_known_args()

    if args.runner_kind.startswith("IMPLEMENTATION"):
        if not (IMPLEMENTATION_V3 / "run_first_v3.py").exists():
            raise FileNotFoundError(
                "selected implementation-v3 runner evidence does not exist: "
                f"{IMPLEMENTATION_V3 / 'run_first_v3.py'}"
            )
        sys.path.insert(0, str(IMPLEMENTATION_V3))
        import run_first_v3 as selected
    else:
        import run_shared_acceptance_first_v1 as selected

    if args.runner_kind.endswith("ABLATION"):
        from aggtrade_acceptance_no_contraction_ablation import (
            build_acceptance_signals_no_contraction,
        )

        selected.build_acceptance_signals = build_acceptance_signals_no_contraction

    sys.argv = [sys.argv[0], *remaining]
    return int(selected.main())


if __name__ == "__main__":
    raise SystemExit(main())
