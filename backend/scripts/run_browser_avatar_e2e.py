"""Exercise a running MuseTalk avatar through a real headless browser."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NODE_DRIVER = PROJECT_ROOT / "frontend" / "scripts" / "browser_avatar_e2e.mjs"
SAMPLE_WAV = PROJECT_ROOT / "data" / "benchmarks" / "samples" / "fleurs_cmn_hans_cn_0000.wav"


def parse_args() -> argparse.Namespace:
    """Parse attach-only browser smoke options without printing the password."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("ARCH_AVATAR_BROWSER_URL", "https://arch.local:8443"),
    )
    parser.add_argument("--password", default=os.environ.get("ARCH_AVATAR_BROWSER_PASSWORD"))
    parser.add_argument("--chrome", type=Path, default=Path("/usr/bin/google-chrome"))
    parser.add_argument("--audio", type=Path, default=SAMPLE_WAV)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    return parser.parse_args()


def reserve_port() -> int:
    """Return an unused loopback port for the temporary Chrome debugger."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def require_file(path: Path, description: str) -> None:
    """Fail before opening a browser when a local prerequisite is absent."""
    if not path.is_file():
        raise FileNotFoundError(f"avatar-browser-smoke: {description} is missing: {path}")


def build_driver_configuration(
    arguments: argparse.Namespace, profile_dir: Path, debug_port: int
) -> dict[str, object]:
    """Build the secret-free-on-output configuration passed to the Node driver."""
    if not arguments.password:
        raise ValueError("Set ARCH_AVATAR_BROWSER_PASSWORD or pass --password.")
    return {
        "url": arguments.url,
        "password": arguments.password,
        "chrome": str(arguments.chrome),
        "audio": str(arguments.audio.resolve()),
        "timeoutSeconds": arguments.timeout_seconds,
        "profileDir": str(profile_dir),
        "debugPort": debug_port,
    }


def main() -> None:
    """Run against an already configured service without changing its runtime state."""
    arguments = parse_args()
    if arguments.timeout_seconds < 30:
        raise SystemExit("avatar-browser-smoke: --timeout-seconds must be at least 30")
    require_file(arguments.chrome, "Chrome binary")
    require_file(arguments.audio, "fixed microphone WAV")
    require_file(NODE_DRIVER, "browser driver")
    with tempfile.TemporaryDirectory(prefix="arch-avatar-browser-") as temporary_dir:
        configuration = build_driver_configuration(
            arguments,
            Path(temporary_dir) / "chrome-profile",
            reserve_port(),
        )
        subprocess.run(
            ["node", str(NODE_DRIVER)],
            input=json.dumps(configuration),
            text=True,
            check=True,
        )


if __name__ == "__main__":
    main()
