"""Run the shared NautilusTrader first week with only retest contraction ablated."""

from __future__ import annotations

import run_shared_acceptance_first_v1 as base_runner
from aggtrade_acceptance_no_contraction_ablation import (
    build_acceptance_signals_no_contraction,
)

base_runner.build_acceptance_signals = build_acceptance_signals_no_contraction


if __name__ == "__main__":
    raise SystemExit(base_runner.main())
