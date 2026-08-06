"""Controlled rerun adapter for the observed eight-column USD-M archive."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SOURCE = Path(__file__).with_name("probe_aggtrades.py")
spec = importlib.util.spec_from_file_location("candidate10_probe_aggtrades", SOURCE)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {SOURCE}")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

# The first controlled run established that historical USD-M archives contain
# the REST aggregate-trade fields plus a final ``is_best_match`` boolean. This
# rerun changes only the schema width/name; date, checksum, aggressor mapping,
# sample limit, instrument, Nautilus engine and callback proof remain fixed.
module.EXPECTED_COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
    "is_best_match",
)


if __name__ == "__main__":
    sys.exit(module.main())
