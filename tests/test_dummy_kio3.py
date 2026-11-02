"""Tests for the standalone, print-only KIO3 container receiver."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from dummy_kio3.server import DummyKIO3Handler, main
from workflow_planner import dispatch_workflow


@pytest.fixture
def endpoint() -> Iterator[str]:
    """Start a disposable receiver for HTTP-level tests.

    Takes no arguments; the operating system selects a free loopback port.

    Yields:
        The base URL of the running receiver; closes the server after the test.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), DummyKIO3Handler)
    worker = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


def test_received_body_is_printed_without_reformatting(
    endpoint: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """Preserve the exact request text and echo all five correlation fields."""
    body = b'{\n  "schema_version": "1.0", "workflow_id": "wf-test",\n  "step_id": "s1", "agent_id": "KIO3", "idempotency_key": "test-key",\n  "task": "Do not execute this", "data": { "example": [1, 2] }\n}'
    request = urllib.request.Request(
        endpoint + "/tasks",
        data=body,
        headers={"Content-Type": "application/json", "Idempotency-Key": "test-key"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        result = json.load(response)
        assert response.status == 200
    assert capfd.readouterr().out == body.decode() + "\n"
    original = json.loads(body)
    for field in (
        "schema_version",
        "workflow_id",
        "step_id",
        "agent_id",
        "idempotency_key",
    ):
        assert result[field] == original[field]
    assert result["status"] == "succeeded"
    assert result["result"] == {
        "dummy": True,
        "received": True,
        "received_bytes": len(body),
    }


def test_kio1_can_dispatch_to_dummy(endpoint: str) -> None:
    """Accept real KIO1 task serialization and return a compatible result."""
    plan = {
        "workflow_id": "wf-dummy-test",
        "execution_mode": "sequential",
        "steps": [
            {
                "step_id": "s1",
                "agent_id": "KIO3",
                "capability": "requirements_engineering",
                "task": "Write requirements",
                "data": {"test": True},
            }
        ],
    }
    result = dispatch_workflow(plan, {"KIO3": {"endpoint": endpoint + "/tasks"}})
    assert result["status"] == "succeeded"
    assert result["steps"][0]["result"]["dummy"] is True


@pytest.mark.parametrize(
    ("body", "key", "status"),
    [
        (b"not json", "key", 400),
        (b"[]", "key", 400),
        (b"{}", "key", 400),
        (
            json.dumps(
                {
                    "schema_version": "2.0",
                    "workflow_id": "wf-test",
                    "step_id": "s1",
                    "agent_id": "KIO3",
                    "idempotency_key": "key",
                }
            ).encode(),
            "key",
            400,
        ),
        (
            json.dumps(
                {
                    "schema_version": "1.0",
                    "workflow_id": "wf-test",
                    "step_id": "s1",
                    "agent_id": "KIO7",
                    "idempotency_key": "key",
                }
            ).encode(),
            "key",
            400,
        ),
        (
            json.dumps(
                {
                    "schema_version": "1.0",
                    "workflow_id": "wf-test",
                    "step_id": "s1",
                    "agent_id": "KIO3",
                    "idempotency_key": "key",
                }
            ).encode(),
            "different-key",
            400,
        ),
        (b"", "key", 400),
        (b"{}", "key", 413),
    ],
)
def test_invalid_task_requests_are_rejected(
    endpoint: str, body: bytes, key: str, status: int
) -> None:
    """Reject malformed or oversized task requests with JSON HTTP errors."""
    request = urllib.request.Request(
        endpoint + "/tasks",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": key,
        },
    )
    if status == 413:
        request.add_header("Content-Length", str(1024 * 1024 + 1))
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=3)
    assert error.value.code == status
    assert "error" in json.load(error.value)


def test_health_endpoint(endpoint: str) -> None:
    """Expose the readiness response used by the Docker health check."""
    with urllib.request.urlopen(endpoint + "/health", timeout=3) as response:
        assert json.load(response) == {
            "status": "ok",
            "agent_id": "KIO3",
            "dummy": True,
        }


@pytest.mark.parametrize(
    ("path", "data", "status"),
    [
        ("/other", None, 404),
        ("/other", b"{}", 404),
        ("/tasks", b"{}", 415),
    ],
)
def test_routes_and_content_type(
    endpoint: str, path: str, data: bytes | None, status: int
) -> None:
    """Only serve the documented health/task paths and JSON task bodies."""
    request = urllib.request.Request(endpoint + path, data=data)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=3)
    assert error.value.code == status


def test_main_starts_server_and_handles_interrupt() -> None:
    """Start on the documented interface and close cleanly on interruption."""
    with patch("dummy_kio3.server.ThreadingHTTPServer") as server:
        server.return_value.__enter__.return_value.serve_forever.side_effect = (
            KeyboardInterrupt()
        )
        main()
        server.assert_called_once_with(("0.0.0.0", 8000), DummyKIO3Handler)
        server.return_value.__exit__.assert_called_once()
