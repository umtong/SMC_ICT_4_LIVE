#!/usr/bin/env python3
"""Install Candidate 05 data contracts, then invoke the V37 portfolio runner."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--work", type=Path, required=True)
known, _ = parser.parse_known_args()
candidate05 = known.work.resolve() / "research" / "candidate-05"
if str(candidate05) not in sys.path:
    sys.path.insert(0, str(candidate05))

from timestamp_contract import install as install_timestamp_contract
from wrangler_contract import install as install_wrangler_contract
from positioning_contract import install as install_positioning_contract
from basis_contract import install as install_basis_contract
from book_depth_gap_contract import install as install_book_depth_gap_contract

install_timestamp_contract()
install_wrangler_contract()
install_positioning_contract()
install_basis_contract()
install_book_depth_gap_contract()

import run_portfolio

raise SystemExit(run_portfolio.main())
