"""Tests for conservative, local JSON candidate extraction."""

import asyncio
import json

import httpx

from arch.memory.extractor import LlamaCppMemoryCandidateExtractor


def test_local_extractor_accepts_only_bounded_json_candidates() -> None:
    """One loopback JSON response produces distinct, reviewable candidate strings."""
    received: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "candidates": [
                                        "用户偏好简洁回答",
                                        " 用户偏好简洁回答 ",
                                        "x" * 321,
                                        42,
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    extractor = LlamaCppMemoryCandidateExtractor(
        "http://127.0.0.1:8081/v1", transport=httpx.MockTransport(handler)
    )
    candidates = asyncio.run(extractor.extract("我喜欢简洁回答", "zh"))

    assert candidates == ("用户偏好简洁回答",)
    assert received["stream"] is False
    assert received["temperature"] == 0.0


def test_local_extractor_discards_invalid_json_and_transport_failures() -> None:
    """Malformed or unavailable local-model output never creates an inferred fallback."""

    def invalid(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    extractor = LlamaCppMemoryCandidateExtractor(
        "http://127.0.0.1:8081/v1", transport=httpx.MockTransport(invalid)
    )
    assert asyncio.run(extractor.extract("记住这个", "zh")) == ()

    def unavailable(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("loopback unavailable")

    unavailable_extractor = LlamaCppMemoryCandidateExtractor(
        "http://127.0.0.1:8081/v1", transport=httpx.MockTransport(unavailable)
    )
    assert asyncio.run(unavailable_extractor.extract("记住这个", "zh")) == ()
