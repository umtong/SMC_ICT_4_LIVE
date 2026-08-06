#!/usr/bin/env python3
"""Official four-instrument runner with trusted venue and risk evidence join."""
from __future__ import annotations

from pathlib import Path

import nt_multi_asset_rich_backtest_v2 as v2
from nt_multi_asset_risk_evidence import reconcile_output


def main() -> None:
    v2.main()
    output = v2._argument_value("--output")
    if output is None:
        raise RuntimeError("--output is required for integrated risk evidence")
    reconcile_output(Path(output))


if __name__ == "__main__":
    main()
