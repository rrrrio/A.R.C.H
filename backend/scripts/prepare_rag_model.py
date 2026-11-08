"""Download the pinned BGE-M3 embedding model into the local data directory."""

from __future__ import annotations

import json

from huggingface_hub import snapshot_download

from local_voice_agent.settings import get_settings

MODEL_REPO = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def main() -> None:
    """Resume the exact model snapshot and record the source identity locally."""
    settings = get_settings()
    settings.ensure_runtime_directories()
    model_root = settings.rag_model_path
    model_root.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=MODEL_REPO, revision=MODEL_REVISION, local_dir=model_root)
    (model_root / "lva-provenance.json").write_text(
        json.dumps({"source_repo": MODEL_REPO, "revision": MODEL_REVISION}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(model_root)


if __name__ == "__main__":
    main()
