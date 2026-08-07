#!/usr/bin/env python3
"""Update only V19 controller frozen blob identities after implementation fixes."""
from __future__ import annotations

import base64
import json
import os
import urllib.request

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
BRANCH = "research/candidate-03"
PATH = "research/candidate-03/run_v19_staged_container.py"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "candidate-03-v19-freeze-fix",
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
    replacements = {
        '"research/candidate-03/derive_nt_lvcfr_v19_signals.py": "819a2d6956c649c9d8c92fadb3f8993aa03cd999"':
            '"research/candidate-03/derive_nt_lvcfr_v19_signals.py": "f01bb00b38f11971019744038df24b8f105989a8"',
        '"research/candidate-03/test_nt_lvcfr_v19.py": "dab9e376256a42460c8e65160279ea0745e0eda2"':
            '"research/candidate-03/test_nt_lvcfr_v19.py": "0b08f3d0935db1644653f3925468b336199186bb"',
    }
    changed = source
    for old, new in replacements.items():
        if old not in changed and new not in changed:
            raise RuntimeError(f"missing frozen-hash target: {old}")
        changed = changed.replace(old, new)
    compile(changed, PATH, "exec")
    if changed == source:
        print("frozen hashes already current")
        return 0
    request(
        "PUT",
        f"https://api.github.com/repos/{REPOSITORY}/contents/{PATH}",
        {
            "message": "candidate-03: refreeze V19 after path-accounting fix",
            "content": base64.b64encode(changed.encode()).decode(),
            "sha": current["sha"],
            "branch": BRANCH,
        },
    )
    print("updated V19 frozen hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
