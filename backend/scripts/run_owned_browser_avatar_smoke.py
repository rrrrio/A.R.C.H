"""Run a temporary authenticated Chrome check against the configured avatar worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from arch.api.auth import router as auth_router
from arch.api.avatar import router as avatar_router
from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.avatar.video import AvatarVideoSignalingService
from arch.settings import PROJECT_ROOT, get_settings

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
NODE_DRIVER = PROJECT_ROOT / "frontend" / "scripts" / "browser_avatar_e2e.mjs"
E2E_HOST = "arch-avatar-e2e.local"


@dataclass(frozen=True)
class RunLayout:
    """Private mutable state owned by one browser renderer smoke."""

    root: Path
    password: str
    caddyfile: Path
    socket_path: Path


def parse_args() -> argparse.Namespace:
    """Parse non-persistent browser smoke options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--chrome", type=Path, default=Path("/usr/bin/google-chrome"))
    return parser.parse_args()


def create_run_layout() -> RunLayout:
    """Create one bounded temporary data directory and ephemeral credentials."""
    e2e_root = PROJECT_ROOT / "data" / "e2e"
    e2e_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="avatar-browser-", dir=e2e_root))
    password = secrets.token_urlsafe(24)
    provision_credentials(root / "auth" / "admin.json", password)
    return RunLayout(
        root=root,
        password=password,
        caddyfile=root / "Caddyfile",
        socket_path=root / "runtime" / "avatar.sock",
    )


def reserve_port() -> int:
    """Return an unused loopback port for a process owned by this smoke."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def require_file(path: Path, description: str) -> None:
    """Fail before starting a temporary process when a required artifact is absent."""
    if not path.is_file():
        raise FileNotFoundError(f"avatar-owned-browser-smoke: {description} is missing: {path}")


def write_caddyfile(path: Path, backend_port: int, https_port: int) -> None:
    """Write the disposable same-origin HTTPS proxy for the isolated API."""
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


def build_worker_command(socket_path: Path) -> list[str]:
    """Build the real renderer worker command using active fixed artifacts."""
    settings = get_settings()
    runtime_python = PROJECT_ROOT / "data/runtime/musetalk/bin/python"
    command = [
        str(runtime_python),
        "-m",
        "avatar_runtime.worker",
        "--socket",
        str(socket_path),
        "--source",
        str(settings.avatar_source_video),
        "--fps",
        str(settings.avatar_fps),
        "--backend",
    ]
    if settings.avatar_worker_backend == "musetalk":
        return command + [
            "musetalk",
            "--model-root",
            str(settings.avatar_model_root),
            "--cache-dir",
            str(settings.avatar_cache_dir),
            "--musetalk-source",
            str(settings.avatar_musetalk_source),
        ]
    if settings.avatar_worker_backend == "wav2lip":
        return command + [
            "wav2lip",
            "--wav2lip-source",
            str(settings.avatar_wav2lip_source),
            "--wav2lip-model",
            str(settings.avatar_wav2lip_model),
            "--wav2lip-avatar",
            str(settings.avatar_wav2lip_avatar),
        ]
    raise ValueError(
        "Owned browser smoke does not support avatar backend: "
        f"{settings.avatar_worker_backend}"
    )


async def wait_for_socket(path: Path, process: subprocess.Popen[bytes]) -> None:
    """Wait for a worker-owned socket while surfacing startup failure immediately."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Avatar worker exited with status {process.returncode}.")
        if path.exists():
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("Avatar worker did not create its private socket.")


async def wait_for_connected(service: AvatarVideoSignalingService) -> None:
    """Wait until the authenticated browser owns the real worker bridge."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if service.status().state == "connected":
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("Browser did not connect to the temporary avatar service.")


def create_app(auth: AuthService, avatar: AvatarVideoSignalingService) -> FastAPI:
    """Build only the authenticated browser routes needed for video validation."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await avatar.close()

    app = FastAPI(lifespan=lifespan)
    app.state.auth_service = auth
    app.state.avatar_video_service = avatar
    app.include_router(auth_router)
    app.include_router(avatar_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


async def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    """Terminate only a subprocess started by this smoke."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        await asyncio.to_thread(process.wait, 10)
    except subprocess.TimeoutExpired:
        process.kill()
        await asyncio.to_thread(process.wait)


async def run(arguments: argparse.Namespace) -> None:
    """Own worker, API, proxy, and Chrome until a real browser frame is decoded."""
    if arguments.timeout_seconds < 30:
        raise ValueError("--timeout-seconds must be at least 30")
    require_file(arguments.chrome, "Chrome binary")
    require_file(FRONTEND_DIST / "index.html", "frontend build output")
    require_file(NODE_DRIVER, "browser driver")
    require_file(PROJECT_ROOT / "data/runtime/musetalk/bin/python", "avatar runtime")
    layout = create_run_layout()
    backend_port = reserve_port()
    https_port = reserve_port()
    caddy_name = f"arch-avatar-e2e-{layout.root.name}"
    worker: subprocess.Popen[bytes] | None = None
    caddy_started = False
    server: uvicorn.Server | None = None
    server_task: asyncio.Task[None] | None = None
    avatar: AvatarVideoSignalingService | None = None
    try:
        layout.socket_path.parent.mkdir(parents=True, exist_ok=True)
        environment = os.environ | {"PYTHONPATH": str(PROJECT_ROOT / "avatar_runtime/src")}
        worker = subprocess.Popen(
            build_worker_command(layout.socket_path), cwd=PROJECT_ROOT, env=environment
        )
        await wait_for_socket(layout.socket_path, worker)
        origin = f"https://{E2E_HOST}:{https_port}"
        avatar = AvatarVideoSignalingService(
            enabled=True,
            model=get_settings().avatar_model,
            source_video=get_settings().avatar_source_video,
            socket_path=layout.socket_path,
            fps=get_settings().avatar_fps,
        )
        app = create_app(AuthService(layout.root / "auth" / "admin.json", origin), avatar)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=backend_port, log_level="warning")
        )
        server_task = asyncio.create_task(server.serve())
        write_caddyfile(layout.caddyfile, backend_port, https_port)
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
        node = await asyncio.create_subprocess_exec(
            "node",
            str(NODE_DRIVER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        configuration = {
            "url": origin,
            "host": E2E_HOST,
            "password": layout.password,
            "chrome": str(arguments.chrome),
            "injectPcm": True,
            "requiredVideoCallbacks": 20
            if get_settings().avatar_worker_backend == "wav2lip"
            else 3,
            "verifyVideoCadence": get_settings().avatar_worker_backend == "wav2lip",
            "minimumFrameGapMs": 15,
            "maximumFrameGapMs": 120,
            "windowSize": "1920,1080"
            if get_settings().avatar_worker_backend == "wav2lip"
            else None,
            "maximumRenderedVideoHeight": 768
            if get_settings().avatar_worker_backend == "wav2lip"
            else None,
            "timeoutSeconds": arguments.timeout_seconds,
            "profileDir": str(layout.root / "chrome-profile"),
            "debugPort": reserve_port(),
        }
        assert node.stdin is not None
        node.stdin.write(json.dumps(configuration).encode())
        await node.stdin.drain()
        node.stdin.close()
        await wait_for_connected(avatar)
        utterance_id = "owned-browser-smoke"
        await avatar.begin(utterance_id, 24000, "neutral")
        await avatar.push_audio(utterance_id, b"\x00\x00" * 24000, 0)
        await avatar.end(utterance_id)
        stdout, stderr = await node.communicate()
        if node.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace"))
        print(stdout.decode("utf-8", errors="replace"), end="")
    finally:
        if server is not None:
            server.should_exit = True
        if server_task is not None:
            await asyncio.gather(server_task, return_exceptions=True)
        if avatar is not None:
            await avatar.close()
        if caddy_started:
            subprocess.run(
                ["docker", "rm", "--force", caddy_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        await stop_process(worker)
        layout.socket_path.unlink(missing_ok=True)
        shutil.rmtree(layout.root, ignore_errors=True)


def main() -> None:
    """Run the owned browser smoke from the command line."""
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
