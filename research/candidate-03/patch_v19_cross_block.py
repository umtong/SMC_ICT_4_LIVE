#!/usr/bin/env python3
"""Apply the controlled V19 cross-block path-accounting correction."""
from __future__ import annotations

import base64
import json
import os
import urllib.request

REPOSITORY = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]
BRANCH = "research/candidate-03"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "candidate-03-v19-cross-block-fix",
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


def update(path: str, replacements: list[tuple[str, str]], message: str) -> None:
    url = f"https://api.github.com/repos/{REPOSITORY}/contents/{path}?ref={BRANCH}"
    current = request("GET", url)
    source = base64.b64decode(current["content"]).decode()
    changed = source
    for old, new in replacements:
        if old not in changed and new not in changed:
            raise RuntimeError(f"missing controlled target in {path}: {old!r}")
        changed = changed.replace(old, new)
    compile(changed, path, "exec")
    if changed == source:
        print("already patched", path)
        return
    request(
        "PUT",
        f"https://api.github.com/repos/{REPOSITORY}/contents/{path}",
        {
            "message": message,
            "content": base64.b64encode(changed.encode()).decode(),
            "sha": current["sha"],
            "branch": BRANCH,
        },
    )
    print("patched", path)


def main() -> int:
    old_path = "\n".join(
        [
            "    path = sum(block.path_bp for block in valid)",
            "    progress = direction * (last / first - 1.0) * 10_000.0",
        ]
    )
    new_path = "\n".join(
        [
            "    path = sum(block.path_bp for block in valid)",
            "    for previous, current in zip(valid, valid[1:]):",
            "        assert previous.last_price is not None and current.first_price is not None",
            "        path += abs(current.first_price / previous.last_price - 1.0) * 10_000.0",
            "    progress = direction * (last / first - 1.0) * 10_000.0",
        ]
    )
    update(
        "research/candidate-03/derive_nt_lvcfr_v19_signals.py",
        [(old_path, new_path)],
        "candidate-03: account for V19 cross-block price path",
    )
    update(
        "research/candidate-03/test_nt_lvcfr_v19.py",
        [
            (
                "second.add(101.0, 1.0, False)\n        second.add(102.0, 1.0, False)",
                "second.add(103.0, 1.0, False)\n        second.add(104.0, 1.0, False)",
            )
        ],
        "candidate-03: test V19 cross-block path continuity",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
