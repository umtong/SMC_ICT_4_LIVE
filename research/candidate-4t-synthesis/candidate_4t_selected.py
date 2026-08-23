#!/usr/bin/env python3
"""Launch candidate 4t using only the action table for each declared window.

Candidate 1k artifacts contain historical diagnostic CSVs inherited from the
research branch as well as the table produced by the current matrix job. Loading
all matching schemas silently mixes periods. For each artifact this adapter
parses the year/month declared in the artifact name and requires exactly one
action table whose event times overlap that window. It then delegates all label
normalization and policy work to candidate_4t_policy.
"""
from __future__ import annotations

import calendar
from pathlib import Path
import re
import tempfile

import pandas as pd

import candidate_4t_policy as policy

ORIGINAL_LOAD_ACTIONS = policy.load_actions
MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_abbr) if name}
MONTHS.update({name.lower(): number for number, name in enumerate(calendar.month_name) if name})
DATE_PATTERN = re.compile(
    r"(20\d{2})[-_](jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?|\d{2})",
    re.IGNORECASE,
)
REQUIRED = {"action_id", "state_id", "episode_id", "order_time_ns"}


def artifact_period(path: Path, root: Path) -> str:
    relative = path.resolve().relative_to(root.resolve())
    return relative.parts[0] if len(relative.parts) > 1 else path.parent.name


def declared_window(name: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    match = DATE_PATTERN.search(name)
    if not match:
        raise ValueError(f"No declared year/month in artifact name: {name}")
    year = int(match.group(1))
    token = match.group(2).lower()
    month = int(token) if token.isdigit() else MONTHS[token]
    start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    return start, end


def select_artifact_table(directory: Path) -> tuple[Path, pd.DataFrame]:
    start, end = declared_window(directory.name)
    matches: list[tuple[Path, pd.DataFrame]] = []
    for path in sorted(directory.rglob("*.csv")):
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        if frame.empty or not REQUIRED.issubset(frame.columns):
            continue
        time = pd.to_numeric(frame.order_time_ns, errors="coerce").dropna()
        if time.empty:
            continue
        minimum = pd.to_datetime(int(time.min()), unit="ns", utc=True)
        maximum = pd.to_datetime(int(time.max()), unit="ns", utc=True)
        if minimum < end and maximum >= start:
            matches.append((path, frame))
    if len(matches) != 1:
        details = [str(path.relative_to(directory)) for path, _ in matches]
        raise RuntimeError(
            f"Expected one current-window action table in {directory.name}; "
            f"found {len(matches)}: {details}"
        )
    return matches[0]


def selected_load_actions(root: Path) -> pd.DataFrame:
    artifacts = sorted(path for path in root.iterdir() if path.is_dir())
    if not artifacts:
        raise FileNotFoundError(f"No artifact directories below {root}")
    with tempfile.TemporaryDirectory(prefix="candidate4t-selected-") as temporary:
        selected_root = Path(temporary)
        manifest: list[dict[str, object]] = []
        for artifact in artifacts:
            source, frame = select_artifact_table(artifact)
            target = selected_root / artifact.name
            target.mkdir(parents=True, exist_ok=True)
            frame.to_csv(target / "actions.csv", index=False)
            manifest.append(
                {
                    "artifact": artifact.name,
                    "source": str(source.relative_to(artifact)),
                    "rows": int(len(frame)),
                    "unique_actions": int(frame.action_id.nunique()),
                    "unique_states": int(frame.state_id.nunique()),
                    "unique_episodes": int(frame.episode_id.nunique()),
                }
            )
        output = ORIGINAL_LOAD_ACTIONS(selected_root)
        output.attrs["selection_manifest"] = manifest
        return output


policy._period_from_path = artifact_period
policy.load_actions = selected_load_actions

if __name__ == "__main__":
    policy.main()
