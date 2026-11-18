"""Wait for a local A.R.C.H. service to report readiness."""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import ssl
import time
from typing import NamedTuple
from urllib.parse import SplitResult, urlsplit

from arch.settings import Settings, get_settings

POLL_INTERVAL_SECONDS = 0.25


class ReadinessTarget(NamedTuple):
    """Describe one fixed loopback request that proves a service is ready."""

    scheme: str
    host: str
    port: int
    host_header: str | None
    tls_server_name: str | None


class LoopbackHTTPSConnection(http.client.HTTPSConnection):
    """Connect to loopback while sending TLS SNI for the configured public origin."""

    def __init__(
        self, connect_host: str, port: int, *, server_hostname: str, context: ssl.SSLContext
    ) -> None:
        super().__init__(server_hostname, port, context=context, timeout=1)
        self._connect_host = connect_host
        self._server_hostname = server_hostname

    def connect(self) -> None:
        self.sock = socket.create_connection((self._connect_host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._server_hostname)


def readiness_target(kind: str, settings: Settings) -> ReadinessTarget:
    """Build a loopback readiness probe for a backend or its HTTPS gateway."""
    if kind == "backend":
        return ReadinessTarget("http", "127.0.0.1", settings.port, None, None)
    if kind == "gateway":
        origin = urlsplit(settings.public_origin)
        return ReadinessTarget(
            "https",
            "127.0.0.1",
            _origin_port(origin),
            origin.netloc,
            origin.hostname,
        )
    raise ValueError(f"Unsupported readiness target: {kind}")


def wait_for_ready(target: ReadinessTarget, timeout_seconds: float) -> None:
    """Poll one local readiness route until it returns the fixed ready response."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _is_ready(target):
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"{target.scheme} readiness probe did not succeed at {target.host}:{target.port}"
    )


def _is_ready(target: ReadinessTarget) -> bool:
    headers = {"Host": target.host_header} if target.host_header is not None else {}
    connection: http.client.HTTPConnection
    if target.scheme == "https":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        assert target.tls_server_name is not None
        connection = LoopbackHTTPSConnection(
            target.host,
            target.port,
            server_hostname=target.tls_server_name,
            context=context,
        )
    else:
        connection = http.client.HTTPConnection(target.host, target.port, timeout=1)
    try:
        connection.request("GET", "/api/ready", headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        return response.status == 200 and payload == {"status": "ready"}
    except (OSError, ValueError, json.JSONDecodeError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def _origin_port(origin: SplitResult) -> int:
    """Use the explicit HTTPS port or its standard default for a validated origin."""
    return origin.port or 443


def main() -> None:
    """Run a bounded readiness check for one locally managed A.R.C.H. service."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("backend", "gateway"), required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    arguments = parser.parse_args()
    target = readiness_target(arguments.target, get_settings())
    wait_for_ready(target, arguments.timeout_seconds)
    print(f"{arguments.target} is ready")


if __name__ == "__main__":
    main()
