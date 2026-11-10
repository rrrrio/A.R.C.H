"""Atomic persistence primitives for one-time local tool approvals."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from arch.agent.core import ToolRequest
from arch.tools.policy import PolicyDecision, ToolPolicy

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "consumed"]
ApprovalAction = Literal["approved", "rejected"]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class ToolApproval:
    id: str
    request_sha256: str
    tool_name: str
    request_arguments_json: str | None
    argv_json: str
    working_directory: str
    reason: str
    status: ApprovalStatus
    created_at: str
    expires_at: str
    decided_at: str | None
    decided_by: str | None


class ApprovalStore:
    """Create, decide, expire, and consume approvals without reusable grants."""

    def __init__(self, database_path: Path, clock: Clock | None = None) -> None:
        self._database_path = database_path
        self._clock = clock or _now

    def create(
        self, request: ToolRequest, policy: ToolPolicy, ttl_seconds: int = 120
    ) -> ToolApproval:
        """Re-evaluate and persist one pending approval for an approval-required request."""
        result = policy.evaluate(request)
        if result.decision is not PolicyDecision.APPROVAL_REQUIRED or result.command is None:
            raise ValueError("Only approval-required executable requests can be approved.")
        if ttl_seconds < 1:
            raise ValueError("Approval TTL must be positive.")
        now = self._clock()
        approval = ToolApproval(
            str(uuid.uuid4()),
            _request_hash(request),
            request.name,
            request.arguments_json,
            json.dumps(result.command.argv, separators=(",", ":")),
            str(result.command.working_directory),
            result.reason,
            "pending",
            now.isoformat(),
            (now + timedelta(seconds=ttl_seconds)).isoformat(),
            None,
            None,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_approvals(
                    id, request_sha256, tool_name, request_arguments_json, argv_json,
                    working_directory, reason,
                    status, created_at, expires_at, decided_at, decided_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.id,
                    approval.request_sha256,
                    approval.tool_name,
                    approval.request_arguments_json,
                    approval.argv_json,
                    approval.working_directory,
                    approval.reason,
                    approval.status,
                    approval.created_at,
                    approval.expires_at,
                    approval.decided_at,
                    approval.decided_by,
                ),
            )
            _record_event(connection, approval.id, "created", "system", approval.created_at)
        return approval

    def list_pending(self) -> tuple[ToolApproval, ...]:
        """Return live pending records after persistently expiring stale approvals."""
        with self._connect() as connection:
            self._expire(connection)
            rows = connection.execute(
                """
                SELECT id, request_sha256, tool_name, request_arguments_json, argv_json,
                       working_directory, reason,
                       status, created_at, expires_at, decided_at, decided_by
                FROM tool_approvals WHERE status = 'pending'
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        return tuple(_approval(row) for row in rows)

    def get(self, approval_id: str) -> ToolApproval | None:
        """Return one current record after making any elapsed authorization visibly expired."""
        with self._connect() as connection:
            self._expire(connection)
            row = _get_approval(connection, approval_id)
        return None if row is None else _approval(row)

    def decide(self, approval_id: str, action: ApprovalAction, actor: str) -> ToolApproval:
        """Approve or reject one still-pending request and retain the actor."""
        if action not in ("approved", "rejected"):
            raise ValueError("Tool approval action must be approved or rejected.")
        if not actor:
            raise ValueError("Tool approval actor cannot be empty.")
        now = self._clock().isoformat()
        with self._connect() as connection:
            self._expire(connection, now)
            cursor = connection.execute(
                """
                UPDATE tool_approvals SET status = ?, decided_at = ?, decided_by = ?
                WHERE id = ? AND status = 'pending' AND expires_at > ?
                """,
                (action, now, actor, approval_id, now),
            )
            if cursor.rowcount != 1:
                _raise_unavailable(connection, approval_id)
            _record_event(connection, approval_id, action, actor, now)
            row = _get_approval(connection, approval_id)
        return _approval(row)

    def cancel_unconsumed(self, approval_id: str) -> ToolApproval | None:
        """Revoke an interrupted approval unless another request has already consumed it."""
        now = self._clock().isoformat()
        with self._connect() as connection:
            self._expire(connection, now)
            cursor = connection.execute(
                """
                UPDATE tool_approvals
                SET status = 'rejected', decided_at = ?, decided_by = 'interrupted_media'
                WHERE id = ? AND status IN ('pending', 'approved') AND expires_at > ?
                """,
                (now, approval_id, now),
            )
            if cursor.rowcount != 1:
                return None
            _record_event(connection, approval_id, "rejected", "interrupted_media", now)
            row = _get_approval(connection, approval_id)
        return _approval(row)

    def consume(self, approval_id: str, request: ToolRequest, actor: str) -> ToolApproval | None:
        """Consume exactly one live approved record bound to the exact original request hash."""
        if not actor:
            raise ValueError("Tool approval actor cannot be empty.")
        now = self._clock().isoformat()
        with self._connect() as connection:
            self._expire(connection, now)
            cursor = connection.execute(
                """
                UPDATE tool_approvals SET status = 'consumed', decided_at = ?, decided_by = ?
                WHERE id = ? AND request_sha256 = ? AND status = 'approved' AND expires_at > ?
                """,
                (now, actor, approval_id, _request_hash(request), now),
            )
            if cursor.rowcount != 1:
                return None
            _record_event(connection, approval_id, "consumed", actor, now)
            row = _get_approval(connection, approval_id)
        return _approval(row)

    def _expire(self, connection: sqlite3.Connection, now: str | None = None) -> None:
        timestamp = now or self._clock().isoformat()
        rows = connection.execute(
            """
            SELECT id FROM tool_approvals
            WHERE status IN ('pending', 'approved') AND expires_at <= ?
            """,
            (timestamp,),
        ).fetchall()
        for (approval_id,) in rows:
            connection.execute(
                "UPDATE tool_approvals SET status = 'expired' WHERE id = ?", (approval_id,)
            )
            _record_event(connection, approval_id, "expired", "system", timestamp)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _now() -> datetime:
    return datetime.now(UTC)


def _record_event(
    connection: sqlite3.Connection, approval_id: str, action: str, actor: str, created_at: str
) -> None:
    connection.execute(
        """
        INSERT INTO tool_approval_events(approval_id, action, actor, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (approval_id, action, actor, created_at),
    )


def _get_approval(connection: sqlite3.Connection, approval_id: str) -> tuple[object, ...] | None:
    row = connection.execute(
        """
        SELECT id, request_sha256, tool_name, request_arguments_json, argv_json,
               working_directory, reason,
               status, created_at, expires_at, decided_at, decided_by
        FROM tool_approvals WHERE id = ?
        """,
        (approval_id,),
    ).fetchone()
    return cast(tuple[object, ...] | None, row)


def _raise_unavailable(connection: sqlite3.Connection, approval_id: str) -> None:
    if _get_approval(connection, approval_id) is None:
        raise KeyError(f"Unknown tool approval: {approval_id}")
    raise ValueError(f"Tool approval is not pending: {approval_id}")


def _approval(row: tuple[object, ...] | None) -> ToolApproval:
    if row is None:
        raise RuntimeError("SQLite tool approval row was not found.")
    (
        approval_id,
        request_sha256,
        tool_name,
        request_arguments_json,
        argv_json,
        working_directory,
        reason,
        status,
        created_at,
        expires_at,
        decided_at,
        decided_by,
    ) = row
    if not (
        isinstance(approval_id, str)
        and isinstance(request_sha256, str)
        and isinstance(tool_name, str)
        and (request_arguments_json is None or isinstance(request_arguments_json, str))
        and isinstance(argv_json, str)
        and isinstance(working_directory, str)
        and isinstance(reason, str)
        and status in ("pending", "approved", "rejected", "expired", "consumed")
        and isinstance(created_at, str)
        and isinstance(expires_at, str)
        and (decided_at is None or isinstance(decided_at, str))
        and (decided_by is None or isinstance(decided_by, str))
    ):
        raise RuntimeError("SQLite returned an invalid tool approval row.")
    return ToolApproval(
        approval_id,
        request_sha256,
        tool_name,
        request_arguments_json,
        argv_json,
        working_directory,
        reason,
        status,
        created_at,
        expires_at,
        decided_at,
        decided_by,
    )


def _request_hash(request: ToolRequest) -> str:
    payload = json.dumps(
        {"name": request.name, "arguments_json": request.arguments_json},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()
