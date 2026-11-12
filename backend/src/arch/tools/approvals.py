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
from arch.tools.policy import HostCommand, PolicyDecision, TerminalProfile, TerminalToolPolicy

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
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class TerminalGrant:
    """A short-lived exact-call authorization owned by one browser session."""

    id: str
    session_id: str
    request_sha256: str
    tool_name: str
    working_directory: str
    access: str
    capabilities: frozenset[str]
    created_at: str
    expires_at: str
    revoked_at: str | None


class ApprovalStore:
    """Create, decide, expire, and consume approvals without reusable grants."""

    def __init__(self, database_path: Path, clock: Clock | None = None) -> None:
        self._database_path = database_path
        self._clock = clock or _now

    def create(
        self,
        request: ToolRequest,
        policy: TerminalToolPolicy,
        ttl_seconds: int = 120,
        profile: TerminalProfile = "assist",
        session_id: str | None = None,
    ) -> ToolApproval:
        """Re-evaluate and persist one pending approval for an approval-required request."""
        result = policy.evaluate_host(request, profile)
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
            session_id,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_approvals(
                    id, request_sha256, tool_name, request_arguments_json, argv_json,
                    working_directory, reason,
                    status, created_at, expires_at, decided_at, decided_by, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    session_id,
                ),
            )
            _record_event(connection, approval.id, "created", "system", approval.created_at)
        return approval

    def grant_for(
        self, session_id: str, request: ToolRequest, command: HostCommand
    ) -> TerminalGrant:
        """Store a 30-minute grant tied to the complete approved tool call."""
        now = self._clock()
        grant = TerminalGrant(
            str(uuid.uuid4()),
            session_id,
            _request_hash(request),
            request.name,
            str(command.working_directory),
            command.access,
            command.capabilities,
            now.isoformat(),
            (now + timedelta(minutes=30)).isoformat(),
            None,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO terminal_grants(
                    id, session_id, request_sha256, tool_name, working_directory, access,
                    capabilities_json, created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.id, grant.session_id, grant.request_sha256, grant.tool_name,
                    grant.working_directory, grant.access,
                    json.dumps(sorted(grant.capabilities)), grant.created_at, grant.expires_at,
                    None,
                ),
            )
        return grant

    def has_grant(
        self, session_id: str | None, request: ToolRequest, command: HostCommand
    ) -> bool:
        """Match an unmodified request to a live grant owned by its current browser session."""
        if session_id is None:
            return False
        now = self._clock().isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE terminal_grants SET revoked_at = ? "
                "WHERE revoked_at IS NULL AND expires_at <= ?",
                (now, now),
            )
            row = connection.execute(
                """
                SELECT id FROM terminal_grants
                WHERE session_id = ? AND request_sha256 = ? AND tool_name = ?
                  AND working_directory = ? AND access = ? AND capabilities_json = ?
                  AND revoked_at IS NULL AND expires_at > ?
                """,
                (
                    session_id,
                    _request_hash(request),
                    request.name,
                    str(command.working_directory),
                    command.access, json.dumps(sorted(command.capabilities)), now,
                ),
            ).fetchone()
        return row is not None

    def list_grants(self, session_id: str) -> tuple[TerminalGrant, ...]:
        """List the authenticated session's remaining exact-call authorizations."""
        now = self._clock().isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE terminal_grants SET revoked_at = ? "
                "WHERE revoked_at IS NULL AND expires_at <= ?",
                (now, now),
            )
            rows = connection.execute(
                """
                SELECT id, session_id, request_sha256, tool_name, working_directory, access,
                       capabilities_json, created_at, expires_at, revoked_at
                FROM terminal_grants WHERE session_id = ? AND revoked_at IS NULL AND expires_at > ?
                ORDER BY created_at DESC
                """,
                (session_id, now),
            ).fetchall()
        return tuple(
            TerminalGrant(
                row[0], row[1], row[2], row[3], row[4], row[5],
                frozenset(json.loads(row[6])), row[7], row[8], row[9]
            )
            for row in rows
        )

    def revoke_grant(self, grant_id: str, session_id: str) -> bool:
        """Revoke one grant only for the browser session that owns it."""
        now = self._clock().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE terminal_grants SET revoked_at = ? "
                "WHERE id = ? AND session_id = ? AND revoked_at IS NULL",
                (now, grant_id, session_id),
            )
        return cursor.rowcount == 1

    def list_pending(self) -> tuple[ToolApproval, ...]:
        """Return live pending records after persistently expiring stale approvals."""
        with self._connect() as connection:
            self._expire(connection)
            rows = connection.execute(
                """
                SELECT id, request_sha256, tool_name, request_arguments_json, argv_json,
                       working_directory, reason,
                       status, created_at, expires_at, decided_at, decided_by, session_id
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
                  AND session_id = ?
                """,
                (now, actor, approval_id, _request_hash(request), now, actor),
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
               status, created_at, expires_at, decided_at, decided_by, session_id
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
        session_id,
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
        and (session_id is None or isinstance(session_id, str))
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
        session_id,
    )


def _request_hash(request: ToolRequest) -> str:
    payload = json.dumps(
        {"name": request.name, "arguments_json": request.arguments_json},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()
