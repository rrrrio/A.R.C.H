"""Run a temporary authenticated Chrome smoke against the installed avatar-media runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from arch.api.auth import router as auth_router
from arch.api.avatar import router as avatar_router
from arch.auth.credentials import provision_credentials
from arch.auth.service import AuthService
from arch.avatar.media import (
    AvatarMediaAnswer,
    AvatarMediaService,
    LoopbackAvatarMediaRuntimeClient,
)
from arch.settings import PROJECT_ROOT, get_settings

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
NODE_DRIVER = PROJECT_ROOT / "frontend" / "scripts" / "browser_avatar_e2e.mjs"
E2E_HOST = "arch-avatar-media-e2e.local"


@dataclass(frozen=True)
class RunLayout:
    """Private mutable state owned by one runtime browser smoke."""

    root: Path
    password: str
    caddyfile: Path


class RecordingAvatarMediaService(AvatarMediaService):
    """Capture only the ephemeral authenticated session ID needed for local PCM injection."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.smoke_session_id: str | None = None
        self.browser_timing_stages: set[str] = set()

    async def offer(self, session_id: str, offer_sdp: str) -> AvatarMediaAnswer:
        answer = await super().offer(session_id, offer_sdp)
        self.smoke_session_id = session_id
        return answer

    def record_browser_timing(self, session_id: str, stage: str, elapsed_ms: int) -> None:
        super().record_browser_timing(session_id, stage, elapsed_ms)
        self.browser_timing_stages.add(stage)


def parse_args() -> argparse.Namespace:
    """Parse non-persistent Chrome smoke options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--chrome", type=Path, default=Path("/usr/bin/google-chrome"))
    return parser.parse_args()


def create_run_layout() -> RunLayout:
    """Create isolated authentication and browser state without touching operator credentials."""
    e2e_root = PROJECT_ROOT / "data" / "e2e"
    e2e_root.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="avatar-media-browser-", dir=e2e_root))
    password = secrets.token_urlsafe(24)
    provision_credentials(root / "auth" / "admin.json", password)
    return RunLayout(root=root, password=password, caddyfile=root / "Caddyfile")


def reserve_port() -> int:
    """Return an unused loopback port for a process owned by this smoke."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def require_file(path: Path, description: str) -> None:
    """Fail before starting any temporary process when an artifact is absent."""
    if not path.is_file():
        raise FileNotFoundError(f"avatar-media-browser-smoke: {description} is missing: {path}")


def write_caddyfile(path: Path, backend_port: int, https_port: int) -> None:
    """Write an isolated same-origin HTTPS proxy exposing only the built frontend and API."""
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


def create_app(auth: AuthService, media: RecordingAvatarMediaService) -> FastAPI:
    """Build the minimum same-origin API that owns media signaling for this smoke."""
    app = FastAPI()
    app.state.auth_service = auth
    app.state.avatar_media_service = media
    app.include_router(auth_router)
    app.include_router(avatar_router)
    return app


async def wait_for_offer(media: RecordingAvatarMediaService) -> str:
    """Wait for the browser session without inspecting runtime-private peer state."""
    for _ in range(120):
        if media.smoke_session_id is not None and media.is_active(media.smoke_session_id):
            return media.smoke_session_id
        await asyncio.sleep(0.25)
    raise TimeoutError("Browser did not establish the temporary avatar media session.")


async def wait_for_browser_timing(media: RecordingAvatarMediaService) -> None:
    """Require both browser-owned decode milestones before declaring the smoke successful."""
    expected = {"browser-first-audio-unmuted", "browser-first-video-decoded"}
    for _ in range(40):
        if expected <= media.browser_timing_stages:
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("Browser did not report both avatar media decode milestones.")


async def run(arguments: argparse.Namespace) -> None:
    """Validate browser playback without changing installed runtime configuration or credentials."""
    if arguments.timeout_seconds < 30:
        raise ValueError("--timeout-seconds must be at least 30")
    require_file(arguments.chrome, "Chrome binary")
    require_file(FRONTEND_DIST / "index.html", "frontend build output")
    require_file(NODE_DRIVER, "browser driver")
    settings = get_settings()
    secret = settings.avatar_media_ticket_secret
    if secret is None:
        raise ValueError("ARCH_AVATAR_MEDIA_TICKET_SECRET is required for this smoke.")
    runtime = LoopbackAvatarMediaRuntimeClient(str(settings.avatar_media_runtime_url))
    if not await runtime.health():
        raise RuntimeError("Installed avatar media runtime is not healthy.")
    layout = create_run_layout()
    backend_port = reserve_port()
    https_port = reserve_port()
    caddy_name = f"arch-avatar-media-e2e-{layout.root.name}"
    caddy_started = False
    server: uvicorn.Server | None = None
    server_task: asyncio.Task[None] | None = None
    node: asyncio.subprocess.Process | None = None
    media = RecordingAvatarMediaService(
        runtime=runtime,
        ticket_secret=secret.get_secret_value().encode("utf-8"),
    )
    try:
        origin = f"https://{E2E_HOST}:{https_port}"
        app = create_app(AuthService(layout.root / "auth" / "admin.json", origin), media)
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
            "chrome": str(arguments.chrome),
            "debugPort": reserve_port(),
            "enableAcceptanceRecorder": True,
            "host": E2E_HOST,
            "injectPcm": True,
            "maximumFrameGapMs": 70,
            "minimumFrameGapMs": 25,
            "password": layout.password,
            "profileDir": str(layout.root / "chrome-profile"),
            "requireSinglePeerAudio": True,
            "requiredVideoCallbacks": 30,
            "timeoutSeconds": arguments.timeout_seconds,
            "url": origin,
            "verifyVideoCadence": True,
        }
        assert node.stdin is not None
        node.stdin.write(json.dumps(configuration).encode())
        await node.stdin.drain()
        node.stdin.close()
        session_id = await wait_for_offer(media)
        await media.submit_utterance(session_id, "avatar-media-browser-smoke", b"\x00\x00" * 48_000)
        stdout, stderr = await node.communicate()
        if node.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace"))
        await wait_for_browser_timing(media)
        print(stdout.decode("utf-8", errors="replace"), end="")
    finally:
        if node is not None and node.returncode is None:
            node.terminate()
            await node.wait()
        if server is not None:
            server.should_exit = True
        if server_task is not None:
            await asyncio.gather(server_task, return_exceptions=True)
        if media.smoke_session_id is not None:
            await media.release(media.smoke_session_id)
        if caddy_started:
            subprocess.run(
                ["docker", "rm", "--force", caddy_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        shutil.rmtree(layout.root, ignore_errors=True)


def main() -> None:
    """Run the owned avatar-media browser smoke from the command line."""
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
