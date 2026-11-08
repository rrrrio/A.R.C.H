"""Best-effort local extraction of reviewable memory candidates."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Protocol

import httpx


class MemoryCandidateExtractor(Protocol):
    """Extract only explicit, reviewable facts from one finalized user turn."""

    async def extract(self, transcript: str, language: str) -> tuple[str, ...]:
        """Return normalized candidate text, or an empty tuple when none is safe to keep."""


class LlamaCppMemoryCandidateExtractor:
    """Call the existing loopback llama.cpp server without affecting media output."""

    def __init__(
        self,
        endpoint: str,
        model: str = "qwen3-8b-q4-k-m",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._transport = transport

    async def extract(self, transcript: str, language: str) -> tuple[str, ...]:
        """Request strict JSON from the local model and reject every ambiguous response."""
        if not transcript.strip():
            return ()
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _EXTRACTION_PROMPT},
                {"role": "user", "content": f"语言：{language}\n用户原话：{transcript}"},
            ],
            "stream": False,
            "temperature": 0.0,
            "max_tokens": 192,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0, transport=self._transport) as client:
                response = await client.post(f"{self._endpoint}/chat/completions", json=payload)
                response.raise_for_status()
                return _candidates_from_response(response.json())
        except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError):
            return ()


_EXTRACTION_PROMPT = """从一条用户原话中提取明确表达且可能长期有效的偏好或事实。
不得推测、概括情绪、引用助手回复或提取临时任务。只输出 JSON 对象，格式严格为：
{"candidates":["候选一","候选二"]}
没有合适候选时输出 {"candidates":[]}。每个候选使用简洁陈述，不超过 320 个字符。"""


def _candidates_from_response(payload: object) -> tuple[str, ...]:
    """Accept only one exact JSON candidate-list shape from an OpenAI-compatible response."""
    if not isinstance(payload, dict):
        return ()
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ()
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        return ()
    decoded = json.loads(message["content"])
    if not isinstance(decoded, dict) or not isinstance(decoded.get("candidates"), list):
        return ()
    return _normalize_candidates(decoded["candidates"])


def _normalize_candidates(values: Sequence[object]) -> tuple[str, ...]:
    """Bound, deduplicate, and preserve only non-empty string candidates."""
    candidates: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate or len(candidate) > 320 or candidate in candidates:
            continue
        candidates.append(candidate)
    return tuple(candidates)
