#!/usr/bin/env python3
"""Run V25 by deterministically specializing the verified V24 controller."""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

TOKEN = os.environ["GITHUB_TOKEN"]
REPOSITORY = os.environ["GITHUB_REPOSITORY"]
REF = os.environ.get("GITHUB_SHA", "research/candidate-03")
SOURCE_PATH = "research/candidate-03/run_v24_staged_container.py"

request = urllib.request.Request(
    f"https://api.github.com/repos/{REPOSITORY}/contents/{SOURCE_PATH}?ref={REF}",
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "candidate-03-v25-external-absorption",
    },
)
with urllib.request.urlopen(request, timeout=120) as response:
    payload = json.loads(response.read().decode())
source = base64.b64decode(payload["content"]).decode("utf-8")

replacements = (
    (
        "candidate-03-nt-lvcfr-v24-mtf-sweep-micro-entry",
        "candidate-03-nt-lvcfr-v25-external-absorption-reversal",
    ),
    (
        "v24-mtf-sweep-micro-stability",
        "v25-external-absorption-stability",
    ),
    (
        "Run independent V24 multi-timeframe sweep micro-entry reversals",
        "Run independent V25 external-liquidity absorption reversals",
    ),
    (
        "candidate-03-v24-mtf-sweep-micro-entry",
        "candidate-03-v25-external-absorption-reversal",
    ),
    (
        "record V24 MTF sweep-micro native evidence",
        "record V25 external-absorption native evidence",
    ),
    (
        "5m external sweep/reclaim -> pre-sweep confirmed 1m pivot CHoCH -> ",
        "3m extreme futures flow with weak price response -> 60m external ",
    ),
    (
        "spot-flow 1m displacement FVG -> 1m retrace defense -> native entry",
        "probe/reclaim -> spot non-acceptance -> midpoint reversal with opposite flow -> native entry",
    ),
    ("sweep_counts", "external_absorption_event_counts"),
    (
        'Path("research/candidate-03/nt_lvcfr_strategy.py").read_bytes()',
        '(base.REPO_ROOT / "research/candidate-03/nt_lvcfr_strategy.py").read_bytes()',
    ),
)
for old, new in replacements:
    if old not in source:
        raise RuntimeError(f"V25 controller specialization anchor missing: {old}")
    source = source.replace(old, new)
source = source.replace("V24", "V25").replace("v24", "v25")

target = Path("/tmp/run_v25_specialized.py")
target.write_text(source, encoding="utf-8")
exec(compile(source, str(target), "exec"), {"__name__": "__main__", "__file__": str(target)})
