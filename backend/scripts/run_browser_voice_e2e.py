"""Run an isolated browser smoke test of the complete local voice path."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from local_voice_agent.auth.credentials import provision_credentials

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
NODE_DRIVER = PROJECT_ROOT / "frontend" / "scripts" / "browser_voice_e2e.mjs"
SAMPLE_WAV = DATA_ROOT / "benchmarks" / "samples" / "fleurs_cmn_hans_cn_0000.wav"
E2E_HOST = "local-voice-agent-e2e.local"


@dataclass(frozen=True)
class RunLayout:
    """Private paths and credentials belonging to one browser acceptance invocation."""

    root: Path
    password: str
    caddyfile: Path
    backend_log: Path


def parse_args() -> argparse.Namespace:
    """Parse explicit overrides without exposing the generated test password."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--chrome", type=Path, default=Path("/usr/bin/google-chrome"))
    parser.add_argument("--audio", type=Path, default=SAMPLE_WAV)
    return parser.parse_args()


def create_run_layout(data_root: Path = DATA_ROOT) -> RunLayout:
    """Create an isolated writable data root without linking shared artifacts into it."""
    e2e_root = data_root / "e2e"
    e2e_root.mkdir(parents=True, exist_ok=True)
    root = e2e_root / secrets.token_hex(8)
    try:
        root.mkdir(mode=0o700)
        password = secrets.token_urlsafe(24)
        provision_credentials(root / "auth" / "admin.json", password)
        return RunLayout(
            root=root,
            password=password,
            caddyfile=root / "Caddyfile",
            backend_log=root / "backend.log",
        )
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def write_caddyfile(path: Path, *, backend_port: int, https_port: int) -> None:
    """Write the disposable same-origin HTTPS proxy for the isolated backend."""
    path.write_text(
        "\n".join(
            (
                "{",
                "    admin off",
                "    auto_https disable_redirects",
                "}",
                "",
                f"https://{E2E_HOST}:{https_port} {{",
                "    tls internal",
                "    bind 127.0.0.1",
                "",
                "    @api path /api/*",
                "    handle @api {",
                f"        reverse_proxy 127.0.0.1:{backend_port}",
                "    }",
                "",
                "    handle {",
                "        root * /srv",
                "        try_files {path} /index.html",
                "        file_server",
                "    }",
                "}",
                "",
            )
        ),
        encoding="utf-8",
    )


def reserve_port() -> int:
    """Choose a currently free loopback port for a short-lived owned process."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class LoopbackHTTPSConnection(http.client.HTTPSConnection):
    """Connect to loopback while preserving the configured hostname as TLS SNI."""

    def __init__(self, host: str, port: int, *, context: ssl.SSLContext, timeout: float) -> None:
        super().__init__(host, port, context=context, timeout=timeout)
        self._ssl_context = context

    def connect(self) -> None:
        socket_connection = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self._ssl_context.wrap_socket(socket_connection, server_hostname=self.host)


def wait_for_health(port: int, timeout_seconds: int) -> None:
    """Wait for the temporary Caddy site without consulting system DNS or CA state."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            connection = LoopbackHTTPSConnection(E2E_HOST, port, context=context, timeout=1)
            connection.request("GET", "/api/health", headers={"Host": f"{E2E_HOST}:{port}"})
            response = connection.getresponse()
            if response.status == 200 and b'"status":"ok"' in response.read():
                connection.close()
                return
            connection.close()
        except OSError:
            pass
        time.sleep(0.25)
    raise TimeoutError("browser-e2e: isolated service did not become healthy")


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    """Terminate only a process started by this invocation."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def require_file(path: Path, description: str) -> None:
    """Fail early with an actionable prerequisite message."""
    if not path.is_file():
        raise FileNotFoundError(f"browser-e2e: {description} is missing: {path}")


def main() -> None:
    """Own all isolated services and report stable browser acceptance milestones."""
    args = parse_args()
    if args.timeout_seconds < 30:
        raise SystemExit("browser-e2e: --timeout-seconds must be at least 30")
    require_file(args.chrome, "Chrome binary")
    require_file(args.audio, "fixed microphone WAV")
    require_file(NODE_DRIVER, "browser driver")
    require_file(FRONTEND_DIST / "index.html", "frontend build output")

    layout = create_run_layout()
    backend_port = reserve_port()
    https_port = reserve_port()
    caddy_name = f"local-voice-agent-e2e-{layout.root.name}"
    backend: subprocess.Popen[bytes] | None = None
    caddy_started = False
    try:
        write_caddyfile(layout.caddyfile, backend_port=backend_port, https_port=https_port)
        environment = os.environ | {
            "LVA_DATA_DIR": str(layout.root),
            "LVA_ARTIFACT_DIR": str(DATA_ROOT),
            "LVA_PUBLIC_ORIGIN": f"https://{E2E_HOST}:{https_port}",
        }
        with layout.backend_log.open("wb") as log_file:
            backend = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "local_voice_agent.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(backend_port),
                ],
                cwd=PROJECT_ROOT / "backend",
                env=environment,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    caddy_name,
                    "--network",
                    "host",
                    "--volume",
                    f"{layout.caddyfile}:/etc/caddy/Caddyfile:ro",
                    "--volume",
                    f"{FRONTEND_DIST}:/srv:ro",
                    "caddy:2.11.8-alpine",
                    "caddy",
                    "run",
                    "--config",
                    "/etc/caddy/Caddyfile",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            caddy_started = True
            wait_for_health(https_port, args.timeout_seconds)
            print("browser-e2e: isolated service healthy")
            configuration = {
                "url": f"https://{E2E_HOST}:{https_port}",
                "password": layout.password,
                "chrome": str(args.chrome),
                "audio": str(args.audio.resolve()),
                "timeoutSeconds": args.timeout_seconds,
                "profileDir": str(layout.root / "chrome-profile"),
                "debugPort": reserve_port(),
            }
            subprocess.run(
                ["node", str(NODE_DRIVER)],
                input=json.dumps(configuration),
                text=True,
                check=True,
            )
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            f"browser-e2e: subprocess failed with exit code {error.returncode}"
        ) from error
    finally:
        stop_process(backend)
        if caddy_started:
            subprocess.run(
                ["docker", "rm", "--force", caddy_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if args.keep_artifacts:
            print(f"browser-e2e: artifacts retained at {layout.root}")
        else:
            shutil.rmtree(layout.root, ignore_errors=True)


if __name__ == "__main__":
    main()
