"""Exercise the configured MuseTalk worker through its Unix and WebRTC boundaries."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import Protocol

from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from av import VideoFrame

from arch.avatar.video import AvatarVideoSignalingService
from arch.settings import PROJECT_ROOT, get_settings


class MuseTalkSettings(Protocol):
    """The renderer settings required to start an isolated smoke worker."""

    avatar_source_video: Path
    avatar_fps: int
    avatar_model_root: Path
    avatar_cache_dir: Path
    avatar_musetalk_source: Path


def build_worker_command(
    runtime_python: Path, socket_path: Path, settings: MuseTalkSettings
) -> list[str]:
    """Build the owned worker invocation from the configured renderer paths."""
    return [
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
        "musetalk",
        "--model-root",
        str(settings.avatar_model_root),
        "--cache-dir",
        str(settings.avatar_cache_dir),
        "--musetalk-source",
        str(settings.avatar_musetalk_source),
    ]


async def wait_for_socket(path: Path, process: subprocess.Popen[bytes]) -> None:
    """Wait for a worker-owned socket while surfacing an early worker failure."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"MuseTalk worker exited with status {process.returncode}.")
        if path.exists():
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("MuseTalk worker did not create its private socket.")


async def smoke(socket_path: Path) -> tuple[int, int, list[float]]:
    """Require three decoded renderer frames at a stable WebRTC cadence."""
    settings = get_settings()
    received: asyncio.Future[tuple[int, int, list[float]]] = (
        asyncio.get_running_loop().create_future()
    )
    peer = RTCPeerConnection()

    @peer.on("track")
    def on_track(track: MediaStreamTrack) -> None:
        async def receive() -> None:
            timestamps: list[float] = []
            dimensions: tuple[int, int] | None = None
            for _ in range(3):
                frame = await track.recv()
                if not isinstance(frame, VideoFrame):
                    if not received.done():
                        received.set_exception(
                            TypeError("MuseTalk smoke peer received a non-video media unit.")
                        )
                    return
                dimensions = (frame.width, frame.height)
                timestamps.append(time.monotonic())
            intervals = [
                right - left for left, right in zip(timestamps, timestamps[1:], strict=False)
            ]
            minimum_interval = 0.75 / settings.avatar_fps
            if any(interval < minimum_interval for interval in intervals):
                if not received.done():
                    received.set_exception(
                        RuntimeError(f"MuseTalk frames arrived in a burst: {intervals!r}")
                    )
                return
            if not received.done() and dimensions is not None:
                received.set_result((*dimensions, intervals))

        asyncio.create_task(receive())

    peer.addTransceiver("video", direction="recvonly")
    offer = await peer.createOffer()
    await peer.setLocalDescription(offer)
    local = peer.localDescription
    assert local is not None and local.sdp is not None
    service = AvatarVideoSignalingService(
        enabled=True,
        model=settings.avatar_model,
        source_video=settings.avatar_source_video,
        socket_path=socket_path,
        fps=settings.avatar_fps,
        queue_max_ms=settings.avatar_queue_max_ms,
        startup_timeout_ms=settings.avatar_startup_timeout_ms,
    )
    try:
        answer = await service.offer("musetalk-smoke", local.sdp, local.type)
        await peer.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )
        await service.begin("musetalk-smoke", 24000, "neutral")
        # Two seconds exceeds the historical 64 KiB line-framing failure boundary.
        await service.push_audio("musetalk-smoke", b"\x00\x00" * 48000, 0)
        return await asyncio.wait_for(received, timeout=30)
    finally:
        await service.close()
        await peer.close()


def main() -> None:
    """Start an owned worker, prove its server-side WebRTC output, then clean up."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--socket",
        type=Path,
        default=PROJECT_ROOT / "data/runtime/avatar/musetalk-smoke.sock",
    )
    parser.add_argument(
        "--existing-worker",
        action="store_true",
        help="Connect to an already supervised worker without starting another GPU process.",
    )
    arguments = parser.parse_args()
    if arguments.existing_worker:
        if not arguments.socket.is_socket():
            raise FileNotFoundError(f"MuseTalk worker socket is missing: {arguments.socket}")
        width, height, intervals = asyncio.run(smoke(arguments.socket))
        print(f"avatar-musetalk-smoke: passed frame={width}x{height} intervals={intervals!r}")
        return
    settings = get_settings()
    runtime_python = PROJECT_ROOT / "data/runtime/musetalk/bin/python"
    if not runtime_python.is_file():
        raise FileNotFoundError(f"MuseTalk runtime is missing: {runtime_python}")
    arguments.socket.unlink(missing_ok=True)
    environment = os.environ | {"PYTHONPATH": str(PROJECT_ROOT / "avatar_runtime/src")}
    process = subprocess.Popen(
        build_worker_command(runtime_python, arguments.socket, settings),
        cwd=PROJECT_ROOT,
        env=environment,
    )
    try:
        asyncio.run(wait_for_socket(arguments.socket, process))
        width, height, intervals = asyncio.run(smoke(arguments.socket))
        print(f"avatar-musetalk-smoke: passed frame={width}x{height} intervals={intervals!r}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        arguments.socket.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
