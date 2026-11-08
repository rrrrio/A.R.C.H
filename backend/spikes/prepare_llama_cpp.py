"""Download and verify the pinned CUDA-enabled llama.cpp server runtime."""

from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import urlopen

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = WORKSPACE_ROOT / "config" / "benchmark-baseline.json"
RUNTIME_ROOT = WORKSPACE_ROOT / "data" / "runtime" / "llama-cpp"


def sha256_file(path: Path) -> str:
    """Return a lower-case SHA-256 digest for a downloaded archive."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_archive(url: str, expected_digest: str, directory: Path) -> Path:
    """Download one archive atomically and reject a digest mismatch."""
    filename = url.rsplit("/", maxsplit=1)[-1]
    destination = directory / filename
    if destination.is_file() and sha256_file(destination) == expected_digest:
        return destination

    with urlopen(url, timeout=60) as response:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := response.read(1024 * 1024):
                temporary.write(chunk)
    if sha256_file(temporary_path) != expected_digest:
        temporary_path.unlink(missing_ok=True)
        raise ValueError(f"Runtime archive digest mismatch: {filename}")
    temporary_path.replace(destination)
    return destination


def extract_archive(archive: Path, destination: Path) -> None:
    """Extract trusted members while allowing only links that remain in destination."""
    resolved_destination = destination.resolve()
    with tarfile.open(archive, mode="r:gz") as source:
        for member in source.getmembers():
            member_path = (destination / member.name).resolve()
            is_inside_destination = (
                resolved_destination in member_path.parents or member_path == resolved_destination
            )
            if not is_inside_destination:
                raise ValueError(f"Unsafe archive member path: {member.name}")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(f"Unsupported archive member type: {member.name}")
            if member.issym() or member.islnk():
                link_target = (member_path.parent / member.linkname).resolve()
                is_safe_link_target = (
                    resolved_destination in link_target.parents
                    or link_target == resolved_destination
                )
                if not is_safe_link_target:
                    raise ValueError(f"Unsafe archive link target: {member.name}")
        source.extractall(destination)


def main() -> None:
    """Prepare both official runtime archives in a single verified directory."""
    baseline: Any = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    runtime = baseline.get("llama_cpp_runtime", {}) if isinstance(baseline, dict) else {}
    build = runtime.get("build")
    binary_url = runtime.get("binary_url")
    binary_digest = runtime.get("binary_sha256")
    cudart_url = runtime.get("cudart_url")
    cudart_digest = runtime.get("cudart_sha256")
    values = (build, binary_url, binary_digest, cudart_url, cudart_digest)
    if not all(isinstance(value, str) for value in values):
        raise SystemExit("The llama.cpp runtime config is incomplete.")

    destination = RUNTIME_ROOT / build
    destination.mkdir(parents=True, exist_ok=True)
    for url, digest in ((binary_url, binary_digest), (cudart_url, cudart_digest)):
        extract_archive(download_archive(url, digest, destination), destination)
    server = destination / "llama-server"
    if not server.is_file():
        raise SystemExit("The verified archives did not provide llama-server.")
    server.chmod(server.stat().st_mode | 0o111)
    print(server)


if __name__ == "__main__":
    main()
