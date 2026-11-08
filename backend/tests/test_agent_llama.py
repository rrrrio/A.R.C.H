"""Tests for local llama.cpp Agent Core streaming without a network dependency."""

import asyncio
import json

import httpx

from local_voice_agent.agent.core import (
    AgentRequest,
    AgentResponseEvent,
    AgentResponseKind,
    ConversationTurn,
)
from local_voice_agent.agent.llama import LlamaCppAgentCore


def test_llama_agent_streams_only_text_and_uses_prior_history() -> None:
    """A local OpenAI-compatible SSE response stays inside the Agent Core contract."""
    received: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=(
                'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"。"}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
        )

    agent = LlamaCppAgentCore("http://127.0.0.1:8081/v1", transport=httpx.MockTransport(handler))
    request = AgentRequest(
        "turn-2",
        "现在怎么样？",
        "zh",
        history=(ConversationTurn("user", "上一次的问题", "turn-1"),),
    )

    async def collect() -> list[AgentResponseEvent]:
        return [event async for event in agent.respond(request)]

    events = asyncio.run(collect())

    assert [event.kind for event in events] == [
        AgentResponseKind.TEXT,
        AgentResponseKind.TEXT,
        AgentResponseKind.END,
    ]
    assert [event.text for event in events] == ["你好", "。", ""]
    assert received["stream"] is True
    assert received["messages"] == [
        {"role": "system", "content": "你是本机语音助手。用简洁的中文回答，避免展示推理过程。"},
        {"role": "user", "content": "上一次的问题"},
        {"role": "user", "content": "现在怎么样？"},
    ]


def test_llama_agent_hides_loopback_transport_errors_from_media() -> None:
    """Connection failures become a generic local error event rather than endpoint details."""
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("loopback unavailable")

    agent = LlamaCppAgentCore("http://127.0.0.1:8081/v1", transport=httpx.MockTransport(handler))

    async def collect() -> list[AgentResponseEvent]:
        request = AgentRequest("turn-1", "你好", "zh")
        return [event async for event in agent.respond(request)]

    events = asyncio.run(collect())

    assert [(event.kind, event.text) for event in events] == [
        (AgentResponseKind.ERROR, "本地语言模型暂时不可用。"),
    ]
