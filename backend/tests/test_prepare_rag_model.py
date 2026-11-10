"""Tests for the explicit local embedding snapshot preparation command."""

import importlib.util
from pathlib import Path
from types import ModuleType

from pytest import MonkeyPatch


def _load_script() -> ModuleType:
    """Load the standalone operator script without executing its command-line entry point."""
    script_path = Path(__file__).parents[1] / "scripts" / "prepare_rag_model.py"
    specification = importlib.util.spec_from_file_location("prepare_rag_model", script_path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_prepare_rag_model_uses_the_configured_data_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A non-default ARCH_DATA_DIR keeps the model and provenance under its own root."""
    module = _load_script()
    model_path = tmp_path / "isolated-data/models/bge-m3/revision"

    class FakeSettings:
        """Supply only the settings surface used by the preparation command."""

        rag_model_path = model_path

        def ensure_runtime_directories(self) -> None:
            pass

    downloaded: list[Path] = []

    def snapshot_download(*, repo_id: str, revision: str, local_dir: Path) -> None:
        assert repo_id == module.MODEL_REPO
        assert revision == module.MODEL_REVISION
        downloaded.append(local_dir)

    monkeypatch.setattr(module, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(module, "snapshot_download", snapshot_download)

    module.main()

    assert downloaded == [model_path]
    assert '"source_repo": "BAAI/bge-m3"' in (model_path / "lva-provenance.json").read_text()
