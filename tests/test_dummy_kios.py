"""Tests for the quiet, configurable KIO2-KIO13 dummy receivers."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from dummy_kios.server import AGENT_IDS, SEPARATOR, DummyKIOServer, main
from prompt_loader import load_capabilities
from workflow_planner import dispatch_workflow, load_agent_registry


def test_registry_covers_all_dummy_agents() -> None:
    """Keep every KIO2-KIO13 endpoint aligned with its Compose service name."""
    path = Path(__file__).parent.parent / "dummy_kios" / "agents.dummy.json"
    registry = load_agent_registry(path)
    assert set(registry) == AGENT_IDS
    for agent_id, settings in registry.items():
        assert settings["endpoint"] == f"http://{agent_id.lower()}:8000/tasks"


@pytest.fixture
def receiver(request: pytest.FixtureRequest) -> Iterator[tuple[str, str]]:
    """Start a disposable receiver for the selected agent.

    Args:
        request: Pytest request with an optional indirect agent-ID parameter.

    Yields:
        Base URL and configured agent ID; closes the server after each test.
    """
    agent_id = getattr(request, "param", "KIO3")
    server = DummyKIOServer(("127.0.0.1", 0), agent_id)
    worker = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", agent_id
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize("receiver", sorted(AGENT_IDS), indirect=True)
def test_each_agent_prints_only_body_and_separator(
    receiver: tuple[str, str], capfd: pytest.CaptureFixture[str]
) -> None:
    """Keep health checks silent and preserve task bytes for every agent."""
    endpoint, agent_id = receiver
    with urllib.request.urlopen(endpoint + "/health", timeout=3) as response:
        assert json.load(response)["agent_id"] == agent_id
    captured = capfd.readouterr()
    assert captured.out == captured.err == ""
    payload = {
        "schema_version": "1.0",
        "workflow_id": "wf-all-dummies",
        "step_id": "s1",
        "agent_id": agent_id,
        "idempotency_key": "test-key",
        "task": "Receive only",
        "data": {"example": "\u03bb", "items": [1, 2]},
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint + "/tasks",
        data=body,
        headers={"Content-Type": "application/json", "Idempotency-Key": "test-key"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        result = json.load(response)
    captured = capfd.readouterr()
    assert captured.out == body.decode("utf-8") + "\n" + SEPARATOR.decode() + "\n"
    assert captured.err == ""
    assert result["status"] == "succeeded"
    assert result["agent_id"] == agent_id
    assert result["result"]["agent_id"] == agent_id
    assert result["result"]["dummy"] is True


@pytest.mark.parametrize("mode", ["sequential", "parallel"])
def test_kio1_can_dispatch_to_all_twelve_agents(mode: str) -> None:
    """Complete real KIO1 HTTP dispatch for every catalog agent."""
    catalog = load_capabilities()
    servers = []
    workers = []
    registry = {}
    try:
        for agent_id in sorted(AGENT_IDS):
            server = DummyKIOServer(("127.0.0.1", 0), agent_id)
            servers.append(server)
            worker = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
            )
            workers.append(worker)
            worker.start()
            registry[agent_id] = {
                "endpoint": f"http://127.0.0.1:{server.server_port}/tasks"
            }
        plan = {
            "workflow_id": "wf-twelve-agents",
            "execution_mode": mode,
            "steps": [
                {
                    "step_id": f"s{index}",
                    "agent_id": agent_id,
                    "capability": catalog[agent_id]["capabilities"][0],
                    "task": "Receive test only",
                }
                for index, agent_id in enumerate(sorted(AGENT_IDS), 1)
            ],
        }
        result = dispatch_workflow(plan, registry)
        assert result["status"] == "succeeded"
        assert len(result["steps"]) == 12
        assert {step["result"]["agent_id"] for step in result["steps"]} == AGENT_IDS
    finally:
        for server in servers:
            server.shutdown()
        for worker in workers:
            worker.join(timeout=2)
        for server in servers:
            server.server_close()


@pytest.mark.parametrize("agent_id", ["KIO1", "KIO14", "", "other"])
def test_invalid_agent_identity_fails_before_binding(agent_id: str) -> None:
    """Reject unsupported identities without starting a listening socket."""
    with pytest.raises(ValueError, match="KIO_AGENT_ID"):
        DummyKIOServer(("127.0.0.1", 0), agent_id)


@pytest.mark.parametrize("path", ["/other", "/tasks"])
def test_invalid_paths_and_content_types_are_quiet(
    receiver: tuple[str, str], path: str, capfd: pytest.CaptureFixture[str]
) -> None:
    """Suppress HTTP access/error output as well as health-check messages."""
    endpoint, agent_id = receiver
    request = urllib.request.Request(endpoint + path, data=b"{}")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=3)
    assert error.value.code == (404 if path == "/other" else 415)
    captured = capfd.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}"])
def test_invalid_json_is_printed_with_separator_then_rejected(
    receiver: tuple[str, str], body: bytes, capfd: pytest.CaptureFixture[str]
) -> None:
    """Print bounded received input without emitting HTTP error logs."""
    endpoint, agent_id = receiver
    request = urllib.request.Request(
        endpoint + "/tasks",
        data=body,
        headers={"Content-Type": "application/json", "Idempotency-Key": "key"},
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=3)
    assert error.value.code == 400
    captured = capfd.readouterr()
    assert captured.out == body.decode() + "\n" + SEPARATOR.decode() + "\n"
    assert captured.err == ""


@pytest.mark.parametrize(
    ("field", "value", "header_key"),
    [
        ("agent_id", "KIO4", "test-key"),
        ("schema_version", "2.0", "test-key"),
        ("idempotency_key", "test-key", "wrong-key"),
    ],
)
def test_misrouted_or_mismatched_tasks_are_rejected_quietly(
    receiver: tuple[str, str],
    field: str,
    value: str,
    header_key: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Never acknowledge a task addressed to another agent or protocol."""
    endpoint, agent_id = receiver
    payload = {
        "schema_version": "1.0",
        "workflow_id": "wf-invalid-task",
        "step_id": "s1",
        "agent_id": agent_id,
        "idempotency_key": "test-key",
    }
    payload[field] = value
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        endpoint + "/tasks",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": header_key,
        },
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=3)
    assert error.value.code == 400
    captured = capfd.readouterr()
    assert captured.out == body.decode() + "\n" + SEPARATOR.decode() + "\n"
    assert captured.err == ""


@pytest.mark.parametrize(
    ("declared_length", "status"),
    [
        ("invalid", 400),
        ("0", 400),
        ("-1", 400),
        (str(1024 * 1024 + 1), 413),
    ],
)
def test_invalid_lengths_are_rejected_without_printing(
    receiver: tuple[str, str],
    declared_length: str,
    status: int,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Reject invalid or oversized declared bodies before reading or printing."""
    endpoint, agent_id = receiver
    request = urllib.request.Request(
        endpoint + "/tasks",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": declared_length,
        },
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=3)
    assert error.value.code == status
    captured = capfd.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("agent_id", sorted(AGENT_IDS))
def test_main_uses_configured_identity_without_startup_output(
    agent_id: str, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Pass each configured identity to the server and keep startup silent."""
    monkeypatch.setenv("KIO_AGENT_ID", agent_id)
    with patch("dummy_kios.server.DummyKIOServer") as server:
        server.return_value.__enter__.return_value.serve_forever.side_effect = (
            KeyboardInterrupt()
        )
        main()
        server.assert_called_once_with(("0.0.0.0", 8000), agent_id)
        server.return_value.__exit__.assert_called_once()
    captured = capfd.readouterr()
    assert captured.out == captured.err == ""
