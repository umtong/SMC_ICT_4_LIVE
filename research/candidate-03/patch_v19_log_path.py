#!/usr/bin/env python3
"""Unify V19 price progress and path length in log-return space."""
from __future__ import annotations

import base64
import json
import os
import urllib.request

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
BRANCH = "research/candidate-03"
PATH = "research/candidate-03/derive_nt_lvcfr_v19_signals.py"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "candidate-03-v19-log-path-fix",
    "Content-Type": "application/json",
}


def request(method: str, url: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        method=method,
        headers=HEADERS,
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode())


def main() -> int:
    url = f"https://api.github.com/repos/{REPOSITORY}/contents/{PATH}?ref={BRANCH}"
    current = request("GET", url)
    source = base64.b64decode(current["content"]).decode()
    replacements = [
        (
            "self.path_bp += abs(price / self.last_price - 1.0) * 10_000.0",
            "self.path_bp += abs(math.log(price / self.last_price)) * 10_000.0",
        ),
        (
            "direction * (block.last_price / block.first_price - 1.0) * 10_000.0",
            "direction * math.log(block.last_price / block.first_price) * 10_000.0",
        ),
        (
            "path += abs(current.first_price / previous.last_price - 1.0) * 10_000.0",
            "path += abs(math.log(current.first_price / previous.last_price)) * 10_000.0",
        ),
        (
            "direction * (last / first - 1.0) * 10_000.0",
            "direction * math.log(last / first) * 10_000.0",
        ),
    ]
    changed = source
    for old, new in replacements:
        if old not in changed and new not in changed:
            raise RuntimeError(f"missing log-path target: {old}")
        changed = changed.replace(old, new)
    compile(changed, PATH, "exec")
    if changed == source:
        print("V19 log path already applied")
        return 0
    request(
        "PUT",
        f"https://api.github.com/repos/{REPOSITORY}/contents/{PATH}",
        {
            "message": "candidate-03: unify V19 progress and path in log space",
            "content": base64.b64encode(changed.encode()).decode(),
            "sha": current["sha"],
            "branch": BRANCH,
        },
    )
    print("applied V19 log-path correction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
