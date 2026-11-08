"""Download the pinned BGE-M3 embedding model into the local data directory."""

from __future__ import annotations

import json
from pathlib import Path

from huggingface_hub import snapshot_download

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_REPO = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_ROOT = PROJECT_ROOT / "data" / "models" / "bge-m3" / MODEL_REVISION


def main() -> None:
    """Resume the exact model snapshot and record the source identity locally."""
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=MODEL_REPO, revision=MODEL_REVISION, local_dir=MODEL_ROOT)
    (MODEL_ROOT / "lva-provenance.json").write_text(
        json.dumps({"source_repo": MODEL_REPO, "revision": MODEL_REVISION}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(MODEL_ROOT)


if __name__ == "__main__":
    main()
