"""Bounded loopback client for SearXNG JSON search results."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One bounded reference returned by the local SearXNG instance."""

    title: str
    url: str
    snippet: str


class SearchUnavailableError(RuntimeError):
    """A bounded loopback search request could not produce a valid response."""


class SearxngSearchClient:
    """Query only a loopback SearXNG instance; never fetch result pages directly."""

    _maximum_response_bytes = 64 * 1024

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("SearXNG timeout must be positive.")
        self._endpoint = _loopback_endpoint(endpoint)
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def search(self, query: str) -> tuple[SearchResult, ...]:
        """Return up to five sanitized web references or raise a generic client error."""
        normalized = _query(query)
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET",
                f"{self._endpoint}/search",
                params={
                    "q": normalized,
                    "format": "json",
                    "categories": "general",
                    "safesearch": "2",
                    "pageno": "1",
                },
            ) as response:
                response.raise_for_status()
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(payload) + len(chunk) > self._maximum_response_bytes:
                        raise SearchUnavailableError("SearXNG response exceeded the size limit.")
                    payload.extend(chunk)
        try:
            return _results(json.loads(payload))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            raise SearchUnavailableError("SearXNG returned an invalid response.") from error


def _loopback_endpoint(value: str) -> str:
    """Accept one HTTP loopback origin without a path, credentials, or query string."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("SearXNG endpoint must be a bare HTTP loopback origin.")
    return value.rstrip("/")


def _query(value: str) -> str:
    """Keep model-generated queries bounded and reject redirect and control syntax."""
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 512
        or "!!" in normalized
        or any(character.isspace() and character not in {" ", "\t"} for character in normalized)
    ):
        raise ValueError("Search query is invalid.")
    return normalized


def _results(value: object) -> tuple[SearchResult, ...]:
    """Defensively retain only valid result entries from the mutable SearXNG response shape."""
    if not isinstance(value, dict):
        raise ValueError("SearXNG returned an invalid response.")
    candidates = value.get("results")
    if not isinstance(candidates, list):
        raise ValueError("SearXNG returned an invalid response.")
    results: list[SearchResult] = []
    for candidate in candidates:
        if len(results) == 5:
            break
        if not isinstance(candidate, dict):
            continue
        title, url, content = candidate.get("title"), candidate.get("url"), candidate.get("content")
        if not isinstance(title, str) or not isinstance(url, str) or not isinstance(content, str):
            continue
        parsed_url = urlsplit(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            continue
        results.append(SearchResult(title[:300], url[:2048], content[:1000]))
    return tuple(results)
