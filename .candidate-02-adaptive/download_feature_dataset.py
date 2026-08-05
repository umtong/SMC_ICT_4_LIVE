"""Download the published deterministic BTCUSDT 5-minute feature set only.

The source repository documents that raw inputs come from Binance Vision and
publishes the feature construction formulas.  Raw forward labels are retained
for research training but are never exposed to live/test decisions.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import json
from huggingface_hub import snapshot_download

REPO_ID = "ibrahimdaud/binance-btcusdt"
REVISION = "main"
ROOT = Path(".cache/candidate-02/hf-btc-features")
OUT = Path("artifacts/candidate-02-hf-features")

def main() -> None:
    local = Path(snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        revision=REVISION,
        allow_patterns=["features/BTCUSDT/*.parquet", "README.md"],
        local_dir=ROOT,
    ))
    files = []
    for path in sorted((local / "features" / "BTCUSDT").glob("*.parquet")):
        files.append({
            "path": str(path),
            "name": path.name,
            "size": path.stat().st_size,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        })
    if not files:
        raise RuntimeError("no BTCUSDT feature parquet files were downloaded")
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "repo_id": REPO_ID,
        "revision": REVISION,
        "allow_patterns": ["features/BTCUSDT/*.parquet", "README.md"],
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
        "files": files,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("repo_id", "revision", "file_count", "total_bytes")}, indent=2))

if __name__ == "__main__":
    main()
