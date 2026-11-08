"""Application-owned orchestration for constrained local tool requests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Literal

from local_voice_agent.agent.core import ToolRequest
from local_voice_agent.tools.approvals import ApprovalStore
from local_voice_agent.tools.policy import PolicyDecision, TerminalToolPolicy
from local_voice_agent.tools.search import SearchResult, SearxngSearchClient
from local_voice_agent.tools.terminal import TerminalExecutor, TerminalResult

ToolOutcomeStatus = Literal["completed", "approval_required", "denied", "unavailable"]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """One bounded outcome that can be rendered without exposing local implementation detail."""

    status: ToolOutcomeStatus
    message: str
    result: TerminalResult | None = None
    search_results: tuple[SearchResult, ...] = ()
    approval_id: str | None = None


class ToolGateway:
    """Own policy evaluation, approval consumption, and bounded terminal execution."""

    def __init__(
        self,
        policy: TerminalToolPolicy,
        approvals: ApprovalStore,
        executor: TerminalExecutor,
        search_client: SearxngSearchClient | None = None,
        max_concurrency: int = 1,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("Tool concurrency must be positive.")
        self._policy = policy
        self._approvals = approvals
        self._executor = executor
        self._search_client = search_client
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def dispatch(self, request: ToolRequest) -> ToolOutcome:
        """Execute auto-safe work or persist a reviewable request with no side effect."""
        if request.name == "web_search":
            return await self._dispatch_search(request)
        policy_result = self._policy.evaluate(request)
        if policy_result.decision is PolicyDecision.DENIED:
            return ToolOutcome("denied", policy_result.reason)
        if policy_result.command is None:
            return ToolOutcome("denied", "The requested tool has no safe command adapter.")
        if policy_result.decision is PolicyDecision.APPROVAL_REQUIRED:
            approval_task = asyncio.create_task(
                asyncio.to_thread(self._approvals.create, request, self._policy)
            )
            try:
                approval = await asyncio.shield(approval_task)
            except asyncio.CancelledError:
                try:
                    approval = await asyncio.shield(approval_task)
                    await asyncio.shield(
                        asyncio.to_thread(self._approvals.cancel_unconsumed, approval.id)
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # If persistence itself failed, there is no approval to expose or retract.
                    pass
                raise
            return ToolOutcome(
                "approval_required",
                "This local tool action requires browser approval.",
                approval_id=approval.id,
            )
        async with self._semaphore:
            result = await self._executor.execute(policy_result.command)
        return ToolOutcome("completed", "Local tool action completed.", result=result)

    async def _dispatch_search(self, request: ToolRequest) -> ToolOutcome:
        """Run only a bounded query through an explicitly enabled loopback SearXNG client."""
        if self._search_client is None:
            return ToolOutcome("denied", "Local web search is disabled.")
        try:
            arguments = json.loads(request.arguments_json)
            if not isinstance(arguments, dict) or set(arguments) != {"query"}:
                raise ValueError
            query = arguments["query"]
            if not isinstance(query, str):
                raise ValueError
            async with self._semaphore:
                results = await self._search_client.search(query)
        except (ValueError, json.JSONDecodeError):
            return ToolOutcome("denied", "Search arguments must contain one query string.")
        except Exception:
            return ToolOutcome("unavailable", "Local web search is temporarily unavailable.")
        return ToolOutcome("completed", "Local web search completed.", search_results=results)

    async def execute_approved(
        self, approval_id: str, request: ToolRequest, actor: str
    ) -> ToolOutcome:
        """Consume one exact approved request before its constrained side effect can begin."""
        policy_result = self._policy.evaluate(request)
        if (
            policy_result.decision is not PolicyDecision.APPROVAL_REQUIRED
            or policy_result.command is None
        ):
            return ToolOutcome(
                "denied", "The approved tool request no longer has a safe command adapter."
            )
        approval = await asyncio.to_thread(self._approvals.consume, approval_id, request, actor)
        if approval is None:
            return ToolOutcome(
                "denied", "The tool approval is missing, expired, or already consumed."
            )
        if approval.argv_json != json.dumps(
            policy_result.command.argv, separators=(",", ":")
        ) or approval.working_directory != str(policy_result.command.working_directory):
            return ToolOutcome(
                "denied", "The approved command no longer matches the stored request."
            )
        async with self._semaphore:
            result = await self._executor._execute_approved(policy_result.command)
        return ToolOutcome("completed", "Approved local tool action completed.", result=result)
