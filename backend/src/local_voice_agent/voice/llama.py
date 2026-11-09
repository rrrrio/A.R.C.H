"""Lifecycle management for the local loopback llama.cpp server."""

from __future__ import annotations

import asyncio
import os
import time

import httpx

from local_voice_agent.voice.models import VoiceModelPaths


class LlamaCppServer:
    """Start and stop one verified llama.cpp server without exposing it to the LAN."""

    def __init__(self, paths: VoiceModelPaths, port: int = 8081) -> None:
        self._paths = paths
        self._port = port
        self._process: asyncio.subprocess.Process | None = None

    @property
    def endpoint(self) -> str:
        """Return the OpenAI-compatible loopback base URL for this server."""
        return f"http://127.0.0.1:{self._port}/v1"

    async def start(self, timeout_seconds: float = 120.0) -> None:
        """Launch the pinned server and wait until its local health endpoint responds."""
        if self._process is not None and self._process.returncode is None:
            return
        self._paths.require_ready()
        environment = os.environ.copy()
        library_path = str(self._paths.llama_server.parent)
        existing = environment.get("LD_LIBRARY_PATH")
        environment["LD_LIBRARY_PATH"] = (
            f"{library_path}:{existing}" if existing else library_path
        )
        self._process = await asyncio.create_subprocess_exec(
            str(self._paths.llama_server),
            "--model",
            str(self._paths.llm_model),
            "--host",
            "127.0.0.1",
            "--port",
            str(self._port),
            "--n-gpu-layers",
            "99",
            "--ctx-size",
            "8192",
            "--jinja",
            "--reasoning",
            "off",
            "--reasoning-format",
            "deepseek",
            cwd=self._paths.llama_server.parent,
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await self._wait_ready(timeout_seconds)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        """Terminate the owned subprocess without touching unrelated llama.cpp instances."""
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def _wait_ready(self, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        async with httpx.AsyncClient(timeout=1.0, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self._process is None:
                    raise RuntimeError("llama.cpp process was not created.")
                if self._process.returncode is not None:
                    raise RuntimeError(f"llama.cpp exited with code {self._process.returncode}.")
                try:
                    response = await client.get(self.endpoint.removesuffix("/v1") + "/health")
                    if response.is_success:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        raise TimeoutError("Timed out waiting for local llama.cpp server readiness.")
