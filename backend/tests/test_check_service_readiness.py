"""Tests for bounded local backend and gateway readiness probes."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    script_path = Path(__file__).parents[1] / "scripts" / "check_service_readiness.py"
    specification = importlib.util.spec_from_file_location("check_service_readiness", script_path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_readiness_targets_pin_network_requests_to_loopback() -> None:
    module = _load_script()

    class FakeSettings:
        port = 8123
        public_origin = "https://arch.local:9443"

    assert module.readiness_target("backend", FakeSettings()) == module.ReadinessTarget(
        "http", "127.0.0.1", 8123, None, None
    )
    assert module.readiness_target("gateway", FakeSettings()) == module.ReadinessTarget(
        "https", "127.0.0.1", 9443, "arch.local:9443", "arch.local"
    )


def test_wait_for_ready_retries_then_accepts_a_fixed_ready_response(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_script()
    target = module.ReadinessTarget("http", "127.0.0.1", 8000, None, None)
    attempts = iter((False, True))
    monkeypatch.setattr(module, "_is_ready", lambda _: next(attempts))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    module.wait_for_ready(target, 1)


def test_wait_for_ready_rejects_non_positive_timeouts() -> None:
    module = _load_script()
    target = module.ReadinessTarget("http", "127.0.0.1", 8000, None, None)

    with pytest.raises(ValueError, match="positive"):
        module.wait_for_ready(target, 0)
