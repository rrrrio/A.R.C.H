"""Private Unix-socket worker for the isolated Qwen voice-clone environment."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import struct
import uuid
from pathlib import Path
from typing import Any

MAX_MESSAGE_BYTES = 32 * 1024 * 1024
MODEL_REVISION = "c27fe8aa05b732b1376d0f6a1e522fbccb84abbd"


def _read_profile(data_dir: Path, profile_id: str) -> tuple[Path, str]:
    """Resolve one regular local reference file without accepting a caller path."""
    if str(uuid.UUID(profile_id)) != profile_id:
        raise ValueError("invalid profile")
    directory = data_dir / "voices" / "profiles" / profile_id
    reference = directory / "reference.wav"
    metadata = directory / "profile.json"
    if (
        not directory.is_dir()
        or directory.is_symlink()
        or not reference.is_file()
        or reference.is_symlink()
    ):
        raise ValueError("missing profile")
    payload: Any = json.loads(metadata.read_text(encoding="utf-8"))
    transcript = payload.get("transcript") if isinstance(payload, dict) else None
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("invalid profile")
    return reference, transcript


async def _read_message(reader: asyncio.StreamReader) -> dict[str, object]:
    """Read one bounded length-prefixed JSON request."""
    size = struct.unpack("!I", await reader.readexactly(4))[0]
    if not 1 <= size <= MAX_MESSAGE_BYTES:
        raise ValueError("invalid request size")
    value: Any = json.loads((await reader.readexactly(size)).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid request")
    return value


async def _write_message(writer: asyncio.StreamWriter, value: dict[str, object]) -> None:
    """Write one bounded length-prefixed JSON response."""
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ValueError("response too large")
    writer.write(struct.pack("!I", len(payload)) + payload)
    await writer.drain()


async def serve(data_dir: Path, socket_path: Path) -> None:
    """Load Qwen once, then serve one local clone request at a time."""
    from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]

    model_dir = data_dir / "models" / "qwen3-tts" / MODEL_REVISION
    model = Qwen3TTSModel.from_pretrained(str(model_dir), device_map="cuda:0")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    lock = asyncio.Lock()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await _read_message(reader)
            text = request.get("text")
            profile_id = request.get("profile_id")
            if not isinstance(text, str) or not text.strip() or not isinstance(profile_id, str):
                raise ValueError("invalid request")
            reference, transcript = _read_profile(data_dir, profile_id)
            async with lock:
                wavs, sample_rate = await asyncio.to_thread(
                    model.generate_voice_clone,
                    text=text,
                    language="Chinese",
                    ref_audio=str(reference),
                    ref_text=transcript,
                )
            pcm = (wavs[0].clip(-1, 1) * 32767).astype("int16").tobytes()
            await _write_message(
                writer,
                {
                    "ok": True,
                    "sample_rate": sample_rate,
                    "audio": base64.b64encode(pcm).decode("ascii"),
                },
            )
        except Exception:
            await _write_message(writer, {"ok": False})
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=str(socket_path), limit=MAX_MESSAGE_BYTES)
    try:
        await server.serve_forever()
    finally:
        server.close()
        await server.wait_closed()
        socket_path.unlink(missing_ok=True)


def main() -> None:
    """Launch the worker only through the isolated runtime client."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.data_dir.resolve(), arguments.socket.resolve()))


if __name__ == "__main__":
    main()
