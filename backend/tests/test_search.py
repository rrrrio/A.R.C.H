"""Tests for the loopback-only SearXNG JSON client."""

import asyncio

import httpx
import pytest

from local_voice_agent.tools.search import SearchUnavailableError, SearxngSearchClient


def test_search_client_uses_bounded_loopback_json_request_and_sanitizes_results() -> None:
    """The tool never follows result URLs or exposes unbounded search payloads."""
    received: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "标题", "url": "https://example.test/a", "content": "摘要"},
                    {"title": "bad", "url": "file:///etc/passwd", "content": "private"},
                ]
            },
        )

    client = SearxngSearchClient(
        "http://127.0.0.1:8080", transport=httpx.MockTransport(handler)
    )
    results = asyncio.run(client.search("本机语音助手"))

    assert received == {
        "q": "本机语音助手",
        "format": "json",
        "categories": "general",
        "safesearch": "2",
        "pageno": "1",
    }
    assert [(result.title, result.url, result.snippet) for result in results] == [
        ("标题", "https://example.test/a", "摘要")
    ]


@pytest.mark.parametrize(
    ("endpoint", "query"),
    [
        ("https://127.0.0.1:8080", "query"),
        ("http://192.168.10.20:8080", "query"),
        ("http://127.0.0.1:8080/api", "query"),
        ("http://127.0.0.1:8080", "!! redirect"),
        ("http://127.0.0.1:8080", "line\nbreak"),
    ],
)
def test_search_client_rejects_non_loopback_endpoints_and_unsafe_queries(
    endpoint: str, query: str
) -> None:
    """The model cannot turn the search client into an arbitrary HTTP client or redirector."""
    if endpoint != "http://127.0.0.1:8080":
        with pytest.raises(ValueError):
            SearxngSearchClient(endpoint)
        return
    client = SearxngSearchClient(
        endpoint, transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    with pytest.raises(ValueError, match="Search query is invalid"):
        asyncio.run(client.search(query))


def test_search_client_rejects_an_oversized_searxng_response_before_json_parsing() -> None:
    """A loopback service cannot make the voice process buffer an unbounded result payload."""
    client = SearxngSearchClient(
        "http://127.0.0.1:8080",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 65537)),
    )

    with pytest.raises(SearchUnavailableError, match="size limit"):
        asyncio.run(client.search("bounded result"))
