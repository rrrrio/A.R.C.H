"""Tests for approval records that gate future local terminal side effects."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arch.agent.core import ToolRequest
from arch.memory.migrations import MigrationRunner
from arch.tools.approvals import ApprovalStore
from arch.tools.policy import TerminalToolPolicy


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 8, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def _request(argv: list[str], cwd: str = ".") -> ToolRequest:
    return ToolRequest("terminal", json.dumps({"argv": argv, "cwd": cwd}))


def _store(tmp_path: Path, clock: _Clock) -> tuple[ApprovalStore, TerminalToolPolicy, Path]:
    database_path = tmp_path / "voice.sqlite3"
    MigrationRunner(database_path).migrate()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return ApprovalStore(database_path, clock), TerminalToolPolicy(workspace), database_path


def test_create_records_exact_normalized_request_and_audit_event(tmp_path: Path) -> None:
    clock = _Clock()
    store, policy, database_path = _store(tmp_path, clock)
    request = _request(["touch", "report.txt"])

    approval = store.create(request, policy)

    assert approval.status == "pending"
    assert json.loads(approval.argv_json) == ["touch", "report.txt"]
    assert approval.working_directory == str((tmp_path / "workspace").resolve())
    assert not (tmp_path / "workspace" / "report.txt").exists()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT action, actor FROM tool_approval_events WHERE approval_id = ?", (approval.id,)
        ).fetchall() == [("created", "system")]


def test_approval_is_one_time_and_bound_to_exact_request(tmp_path: Path) -> None:
    clock = _Clock()
    store, policy, _ = _store(tmp_path, clock)
    request = _request(["touch", "report.txt"])
    approval = store.create(request, policy)

    assert store.consume(approval.id, request, "executor") is None
    assert store.decide(approval.id, "approved", "administrator").status == "approved"
    assert store.consume(approval.id, _request(["touch", "other.txt"]), "executor") is None
    consumed = store.consume(approval.id, request, "executor")

    assert consumed is not None
    assert consumed.status == "consumed"
    assert store.consume(approval.id, request, "executor") is None


def test_interruption_revokes_a_browser_approved_but_unconsumed_request(tmp_path: Path) -> None:
    """Cancellation wins over a pending execution because both state changes are atomic."""
    clock = _Clock()
    store, policy, _ = _store(tmp_path, clock)
    request = _request(["touch", "report.txt"])
    approval = store.create(request, policy)

    assert store.decide(approval.id, "approved", "administrator").status == "approved"
    revoked = store.cancel_unconsumed(approval.id)

    assert revoked is not None
    assert revoked.status == "rejected"
    assert revoked.decided_by == "interrupted_media"
    assert store.consume(approval.id, request, "executor") is None


def test_expiry_invalidates_pending_approval_and_is_audited(tmp_path: Path) -> None:
    clock = _Clock()
    store, policy, database_path = _store(tmp_path, clock)
    request = _request(["mkdir", "reports"])
    approval = store.create(request, policy, ttl_seconds=10)
    clock.value += timedelta(seconds=10)

    assert store.list_pending() == ()
    with pytest.raises(ValueError, match="not pending"):
        store.decide(approval.id, "approved", "administrator")
    assert store.consume(approval.id, request, "executor") is None
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT action FROM tool_approval_events WHERE approval_id = ? ORDER BY id",
            (approval.id,),
        ).fetchall() == [("created",), ("expired",)]


def test_unknown_or_invalid_decisions_are_rejected(tmp_path: Path) -> None:
    clock = _Clock()
    store, policy, _ = _store(tmp_path, clock)
    request = _request(["cat", "report.txt"])
    approval = store.create(request, policy)

    with pytest.raises(KeyError, match="Unknown tool approval"):
        store.decide("missing", "approved", "administrator")
    with pytest.raises(ValueError, match="approved or rejected"):
        store.decide(approval.id, "expired", "administrator")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="actor cannot be empty"):
        store.decide(approval.id, "approved", "")


def test_auto_or_denied_request_cannot_be_forced_into_the_approval_store(tmp_path: Path) -> None:
    """The store owns policy evaluation so callers cannot pair one request with another command."""
    clock = _Clock()
    store, policy, _ = _store(tmp_path, clock)

    with pytest.raises(ValueError, match="approval-required executable"):
        store.create(_request(["pwd"]), policy)
    with pytest.raises(ValueError, match="approval-required executable"):
        store.create(_request(["rm", "report.txt"]), policy)
