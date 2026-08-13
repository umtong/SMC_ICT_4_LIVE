#!/usr/bin/env python3
from __future__ import annotations

import run_binance_demo_re1 as _base
from variant_loader_v2 import load_object, selected_variant


def main() -> None:
    _, spec = selected_variant()
    _base.EasyChartRE1FreshBundle = load_object(spec.bundle)
    _base.EasyChartRE1CoherentPaperStrategy = load_object(spec.paper_strategy)
    _base.main()


if __name__ == "__main__":
    main()
