#!/usr/bin/env python3
from __future__ import annotations

import run_mtf_backtest_re1 as _runner
from variant_loader_v4 import load_object, selected_variant


def main() -> None:
    _, spec = selected_variant()
    _runner.EasyChartRE1NaturalBundle = load_object(spec.bundle)
    _runner.EasyChartRE1StructuralStrategy = load_object(spec.venue_strategy)
    _runner.main()


if __name__ == "__main__":
    main()
