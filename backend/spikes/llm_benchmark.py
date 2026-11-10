"""Measure first generated content and throughput from a local Ollama server."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

from arch.benchmarks.metrics import summary_dict
from arch.benchmarks.reporting import write_json_report


def parse_args() -> argparse.Namespace:
    """Parse a deterministic prompt and local Ollama connection settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--protocol", choices=("ollama", "openai"), default="ollama")
    parser.add_argument("--prompt", default="用一句话说明本机语音助手的健康状态。")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def run_once(client: httpx.Client, endpoint: str, model: str, prompt: str) -> dict[str, Any]:
    """Consume Ollama JSONL output and retain server-reported token metrics."""
    payload = {"model": model, "prompt": prompt, "stream": True, "options": {"num_ctx": 8192}}
    started = time.perf_counter()
    first_content_seconds: float | None = None
    final_event: dict[str, Any] | None = None
    with client.stream("POST", f"{endpoint.rstrip('/')}/api/generate", json=payload) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            event = json.loads(line)
            if first_content_seconds is None and event.get("response"):
                first_content_seconds = time.perf_counter() - started
            if event.get("done"):
                final_event = event

    if first_content_seconds is None or final_event is None:
        raise RuntimeError("Ollama stream ended without content and a final event.")
    return {
        "first_content_seconds": first_content_seconds,
        "total_seconds": time.perf_counter() - started,
        "eval_count": final_event.get("eval_count"),
        "eval_duration_ns": final_event.get("eval_duration"),
    }


def run_openai_once(client: httpx.Client, endpoint: str, model: str, prompt: str) -> dict[str, Any]:
    """Consume llama-server OpenAI-compatible Server-Sent Events."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "temperature": 0.6,
        "top_p": 0.95,
        "max_tokens": 128,
    }
    started = time.perf_counter()
    first_content_seconds: float | None = None
    generated_characters = 0
    with client.stream(
        "POST", f"{endpoint.rstrip('/')}/v1/chat/completions", json=payload
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            encoded_event = line.removeprefix("data: ")
            if encoded_event == "[DONE]":
                continue
            event = json.loads(encoded_event)
            choices = event.get("choices", [])
            delta = choices[0].get("delta", {}) if choices else {}
            content = delta.get("content", "")
            if not isinstance(content, str) or not content:
                continue
            if first_content_seconds is None:
                first_content_seconds = time.perf_counter() - started
            generated_characters += len(content)

    if first_content_seconds is None:
        raise RuntimeError("OpenAI-compatible stream ended without generated content.")
    return {
        "first_content_seconds": first_content_seconds,
        "total_seconds": time.perf_counter() - started,
        "generated_characters": generated_characters,
    }


def main() -> None:
    """Run warm-up plus repeated streaming requests against a local server."""
    args = parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")

    runner = run_once if args.protocol == "ollama" else run_openai_once
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=5.0)) as client:
        warmup = runner(client, args.endpoint, args.model, args.prompt)
        runs = [runner(client, args.endpoint, args.model, args.prompt) for _ in range(args.runs)]

    tokens_per_second = [
        event["eval_count"] / (event["eval_duration_ns"] / 1_000_000_000)
        for event in runs
        if isinstance(event.get("eval_count"), int)
        and isinstance(event.get("eval_duration_ns"), int)
        and event["eval_duration_ns"] > 0
    ]
    report: dict[str, Any] = {
        "benchmark": "llm",
        "endpoint": args.endpoint,
        "model": args.model,
        "protocol": args.protocol,
        "prompt": args.prompt,
        "warmup": warmup,
        "first_content_seconds": summary_dict([event["first_content_seconds"] for event in runs]),
        "total_seconds": summary_dict([event["total_seconds"] for event in runs]),
    }
    if tokens_per_second:
        report["server_reported_tokens_per_second"] = summary_dict(tokens_per_second)
    generated_characters_per_second = [
        event["generated_characters"] / event["total_seconds"]
        for event in runs
        if isinstance(event.get("generated_characters"), int) and event["total_seconds"] > 0
    ]
    if generated_characters_per_second:
        report["generated_characters_per_second"] = summary_dict(generated_characters_per_second)
    write_json_report(args.output, report)


if __name__ == "__main__":
    main()
