#!/usr/bin/env python3
"""Make the aggTrades loader streaming and schema-tolerant."""
from __future__ import annotations

from pathlib import Path

MARKER = "C11_MICRO_STREAMING_AGGTRADES"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


def apply(root: Path) -> int:
    path = root / "run_microstructure_nautilus.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return 0
    source = replace_once(
        source,
        "import json\nfrom pathlib import Path\n",
        "import json\nfrom pathlib import Path\nimport shutil\n",
        "shutil import",
    )
    source = replace_once(
        source,
        '''            with urlopen(request, timeout=180) as response:  # noqa: S310 fixed HTTPS archive host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"small archive response: {url}")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(payload)
            with ZipFile(temporary) as archive:
''',
        '''            # C11_MICRO_STREAMING_AGGTRADES: daily aggregate-trade
            # archives can be large. Stream directly to disk rather than keeping
            # a second full compressed copy in process memory.
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            with urlopen(request, timeout=180) as response, temporary.open("wb") as stream:  # noqa: S310 fixed HTTPS archive host
                shutil.copyfileobj(response, stream, length=1024 * 1024)
            if temporary.stat().st_size < 100:
                raise RuntimeError(f"small archive response: {url}")
            with ZipFile(temporary) as archive:
''',
        "streaming download",
    )
    source = replace_once(
        source,
        '''def aggregate_day(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive members for {path.name}: {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(BytesIO(payload))
    if not set(AGG_COLUMNS).issubset(frame.columns):
        frame = pd.read_csv(BytesIO(payload), header=None, names=AGG_COLUMNS)
''',
        '''def aggregate_day(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive members for {path.name}: {members}")
        member = members[0]
        with archive.open(member) as stream:
            frame = pd.read_csv(stream)
        if not set(AGG_COLUMNS).issubset(frame.columns):
            # Reopen the member because the first parser consumed the stream.
            # usecols tolerates future trailing archive fields while preserving
            # the stable first seven Binance aggregate-trade columns.
            with archive.open(member) as stream:
                frame = pd.read_csv(
                    stream,
                    header=None,
                    names=AGG_COLUMNS,
                    usecols=range(len(AGG_COLUMNS)),
                )
''',
        "streaming CSV parse",
    )
    path.write_text(source, encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    print(f"microstructure loader fix applied: {apply(root)}")


if __name__ == "__main__":
    main()
