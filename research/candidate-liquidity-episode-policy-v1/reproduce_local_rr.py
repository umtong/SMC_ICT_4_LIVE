#!/usr/bin/env python3
"""Run the standard episode reproduction stack with risk-aware destinations."""
from __future__ import annotations

import sys

import episode_policy_local_rr as local_policy

# ``reproduce`` imports the policy by its historical module name.  Present the
# risk-aware implementation under that name without duplicating the data,
# accounting or execution machinery.
sys.modules["episode_policy"] = local_policy

import reproduce  # noqa: E402


if __name__ == "__main__":
    reproduce.main()
