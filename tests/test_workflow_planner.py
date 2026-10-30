"""Focused contract, dependency, and transport tests for container dispatch."""

import http.client
import json
import logging
import threading
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from prompt_loader import load_capabilities, load_prompt
from workflow_planner import (
    WorkflowConfigurationError,
    _NoRedirect,
    _send_task,
    dispatch_workflow,
    load_agent_registry,
    validate_plan,
)


def make_plan(mode: str = "sequential") -> dict:
    """Build a two-agent plan using catalog capabilities."""
    return {
        "workflow_id": "wf-test",
        "execution_mode": mode,
        "steps": [
            {
                "step_id": "s1",
                "agent_id": "KIO7",
                "capability": "code_generation",
                "task": "Write service",
            },
            {
                "step_id": "s2",
                "agent_id": "KIO11",
                "capability": "validation",
                "task": "Test service",
            },
        ],
    }


REGISTRY = {
    "KIO7": {"endpoint": "http://kio7:8000/tasks"},
    "KIO11": {"endpoint": "http://kio11:8000/tasks"},
}


def test_load_capabilities_default_catalog() -> None:
    """Verify the bundled catalog has all supported agents and capabilities."""
    catalog = load_capabilities()
    assert set(catalog) == {f"KIO{number}" for number in range(2, 14)}
    assert "code_generation" in catalog["KIO7"]["capabilities"]


def test_bundled_prompt_expands_shared_catalog() -> None:
    """Keep the shipped system prompt synchronized with every catalog entry."""
    path = Path(__file__).parent.parent / "prompts" / "kio1_system_prompt_ver1.txt"
    rendered = load_prompt(str(path))
    assert "{{KIO_CAPABILITIES}}" not in rendered
    for agent_id, definition in load_capabilities().items():
        assert agent_id in rendered
        assert definition["description"] in rendered
        assert all(capability in rendered for capability in definition["capabilities"])


def test_load_capabilities_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Load a replacement catalog without changing application code."""
    path = tmp_path / "capabilities.json"
    catalog = {
        "KIO7": {"description": "Custom generator", "capabilities": ["custom_codegen"]}
    }
    path.write_text(json.dumps(catalog))
    monkeypatch.setenv("KIO1_CAPABILITIES_PATH", str(path))
    assert load_capabilities() == catalog
    monkeypatch.setenv("KIO1_CAPABILITIES_PATH", "missing.json")
    assert load_capabilities(path) == catalog


@pytest.mark.parametrize(
    "catalog",
    [
        [],
        {},
        {"KIO1": {"description": "Invalid", "capabilities": ["x"]}},
        {"KIO7": []},
        {"KIO7": {"description": "", "capabilities": ["x"]}},
        {"KIO7": {"description": "Generator", "capabilities": []}},
        {"KIO7": {"description": "Generator", "capabilities": ["x", "x"]}},
        {"KIO7": {"description": "Generator", "capabilities": [{}]}},
        {"KIO7": {"description": "Generator", "capabilities": [" "]}},
    ],
)
def test_load_capabilities_rejects_invalid_catalog(
    tmp_path: Path, catalog: object
) -> None:
    """Reject invalid catalog structures before prompts or tasks consume them."""
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError, match="Invalid capability catalog"):
        load_capabilities(path)


@pytest.mark.parametrize("content", [None, "not-json"])
def test_load_capabilities_rejects_unreadable_catalog(
    tmp_path: Path, content: str | None
) -> None:
    """Report missing and malformed catalog files consistently."""
    path = tmp_path / "capabilities.json"
    if content is not None:
        path.write_text(content)
    with pytest.raises(ValueError, match="Invalid capability catalog"):
        load_capabilities(path)


def test_replacement_catalog_controls_prompt_and_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use exactly the same replacement capabilities for prompting and dispatch."""
    path = tmp_path / "capabilities.json"
    path.write_text(
        json.dumps(
            {
                "KIO7": {
                    "description": "Custom generator",
                    "capabilities": ["custom_codegen"],
                },
                "KIO11": {"description": "Tester", "capabilities": ["validation"]},
            }
        )
    )
    monkeypatch.setenv("KIO1_CAPABILITIES_PATH", str(path))
    template = tmp_path / "prompt.txt"
    template.write_text("Agents:\n{{KIO_CAPABILITIES}}")
    rendered = load_prompt(str(template))
    assert "custom_codegen" in rendered
    assert "Custom generator" in rendered
    assert "{{KIO_CAPABILITIES}}" not in rendered
    plan = make_plan()
    with pytest.raises(ValueError, match="capability"):
        validate_plan(plan)
    plan["steps"][0]["capability"] = "custom_codegen"
    assert validate_plan(plan)[0]["capability"] == "custom_codegen"
    registry_path = tmp_path / "agents.json"
    registry_path.write_text(json.dumps(REGISTRY))
    assert load_agent_registry(registry_path) == REGISTRY


def test_invalid_catalog_prevents_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not send tasks or accept routing against a broken catalog."""
    monkeypatch.setenv("KIO1_CAPABILITIES_PATH", str(tmp_path / "missing.json"))
    with patch("workflow_planner._send_task") as sender:
        with pytest.raises(ValueError, match="catalog"):
            dispatch_workflow(make_plan(), REGISTRY)
        sender.assert_not_called()
    registry_path = tmp_path / "agents.json"
    registry_path.write_text(json.dumps(REGISTRY))
    with pytest.raises(WorkflowConfigurationError):
        load_agent_registry(registry_path)


def succeeded(endpoint: str, token_env: str | None, payload: dict) -> dict:
    """Return a correlated fake response without executing a container."""
    return {
        **payload,
        "status": "succeeded",
        "result": {"artifact": payload["step_id"]},
    }


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "unknown"])
def test_workflow_logs_timing_outcomes_and_blocked_steps(
    caplog: pytest.LogCaptureFixture, outcome: str
) -> None:
    """Log execution performance and aggregate outcomes without task contents."""
    with patch("workflow_planner._send_task") as sender:
        sender.side_effect = succeeded if outcome == "succeeded" else None
        sender.return_value = {"status": outcome, "error": {"code": "TEST_ERROR"}}
        with caplog.at_level(logging.INFO, logger="workflow_planner"):
            result = dispatch_workflow(make_plan(), REGISTRY)
    assert (
        f"Workflow completed: workflow_id=wf-test status={outcome} duration_ms="
        in caplog.text
    )
    assert (
        "Workflow started:" in caplog.text and "validation_duration_ms=" in caplog.text
    )
    assert "Workflow batch ready:" in caplog.text
    assert "Write service" not in caplog.text
    if outcome == "succeeded":
        assert "step_count=2 succeeded=2 failed=0 unknown=0 blocked=0" in caplog.text
    else:
        assert (
            "Task blocked: workflow_id=wf-test step_id=s2 agent_id=KIO11" in caplog.text
        )
        assert "blocked=1" in caplog.text
        assert "error_code='TEST_ERROR'" in caplog.text
        assert result["steps"][1]["status"] == "blocked"


def test_rejected_workflow_logs_validation_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Make preflight failures visible without logging untrusted plan contents."""
    with caplog.at_level(logging.INFO, logger="workflow_planner"):
        with pytest.raises(ValueError):
            dispatch_workflow({"private": "private-plan-data"}, REGISTRY)
    assert "Workflow rejected: duration_ms=" in caplog.text
    assert "error_type=ValueError" in caplog.text
    assert "private-plan-data" not in caplog.text


@patch("workflow_planner._send_task", side_effect=succeeded)
def test_sequential_dispatch_passes_dependency_outputs(sender: MagicMock) -> None:
    """Forward predecessor results, versioning, and stable idempotency keys."""
    result = dispatch_workflow(make_plan(), REGISTRY)
    assert result["status"] == "succeeded"
    first = sender.call_args_list[0].args[2]
    second = sender.call_args_list[1].args[2]
    assert first["schema_version"] == "1.0"
    assert "endpoint" not in first
    assert second["dependency_results"] == {"s1": {"artifact": "s1"}}
    assert len(first["idempotency_key"]) == 64
    assert "deadline_at" in first
    dispatch_workflow(make_plan(), REGISTRY)
    assert (
        sender.call_args_list[2].args[2]["idempotency_key"] == first["idempotency_key"]
    )


@pytest.mark.parametrize("mode", ["parallel", "mixed"])
@patch("workflow_planner._send_task", side_effect=succeeded)
def test_independent_tasks_have_no_dependency_results(
    sender: MagicMock, mode: str
) -> None:
    """Schedule independent tasks in both supported nonsequential modes."""
    plan = make_plan(mode)
    for step in plan["steps"]:
        step["depends_on"] = []
    assert dispatch_workflow(plan, REGISTRY)["status"] == "succeeded"
    assert all(
        call.args[2]["dependency_results"] == {} for call in sender.call_args_list
    )


@patch("workflow_planner._send_task", return_value={"status": "failed"})
def test_failure_blocks_remaining_steps(sender: MagicMock) -> None:
    """Do not run downstream work after a failed dependency."""
    result = dispatch_workflow(make_plan(), REGISTRY)
    assert result["status"] == "failed"
    assert result["steps"][1]["status"] == "blocked"
    sender.assert_called_once()


@pytest.mark.parametrize(
    "defect",
    ["cycle", "unknown", "capability", "endpoint", "duplicate", "missing_dependencies"],
)
@patch("workflow_planner._send_task")
def test_invalid_plan_never_sends(sender: MagicMock, defect: str) -> None:
    """Validate the entire plan, including later steps, before any dispatch."""
    plan = make_plan("mixed")
    plan["steps"][0]["depends_on"] = []
    plan["steps"][1]["depends_on"] = ["s1"]
    if defect == "cycle":
        plan["steps"][0]["depends_on"] = ["s2"]
    elif defect == "unknown":
        plan["steps"][1]["depends_on"] = ["missing"]
    elif defect == "capability":
        plan["steps"][1]["capability"] = "code_generation"
    elif defect == "endpoint":
        plan["steps"][1]["endpoint"] = "http://untrusted/tasks"
    elif defect == "duplicate":
        plan["steps"][1]["step_id"] = "s1"
    else:
        del plan["steps"][1]["depends_on"]
    with pytest.raises(ValueError):
        dispatch_workflow(plan, REGISTRY)
    sender.assert_not_called()


@patch("workflow_planner._send_task")
def test_missing_agent_configuration_never_sends(sender: MagicMock) -> None:
    """Reject incomplete routing configuration before starting work."""
    with pytest.raises(WorkflowConfigurationError):
        dispatch_workflow(make_plan(), {"KIO7": REGISTRY["KIO7"]})
    sender.assert_not_called()


def test_registry_validation(tmp_path: Path) -> None:
    """Accept trusted HTTP routes but reject file URLs and missing files."""
    registry_path = tmp_path / "agents.json"
    registry_path.write_text(json.dumps(REGISTRY))
    assert load_agent_registry(registry_path) == REGISTRY
    registry_path.write_text('{"KIO7": {"endpoint": "file:///etc/passwd"}}')
    with pytest.raises(WorkflowConfigurationError):
        load_agent_registry(registry_path)
    with pytest.raises(WorkflowConfigurationError):
        load_agent_registry(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "reply",
    [
        "valid",
        "mismatch",
        "non_json",
        "oversized",
        "async",
        "timeout",
        "failure",
        "non_object",
        "nonterminal",
        "bad_result",
        "bad_error",
        "invalid_utf8",
        "nonfinite",
        "http_error",
    ],
)
@patch("workflow_planner.urllib.request.build_opener")
def test_transport_enforces_contract(
    opener: MagicMock, reply: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Reject malformed, oversized, asynchronous, or uncorrelated replies."""
    payload = {
        "schema_version": "1.0",
        "workflow_id": "wf-test",
        "step_id": "s1",
        "agent_id": "KIO7",
        "idempotency_key": "abc",
    }
    result = {**payload, "status": "succeeded", "result": {}}
    if reply == "mismatch":
        result["step_id"] = "other"
    if reply == "failure":
        result.update(
            status="failed",
            error={"code": "INVALID_INPUT", "message": "No input", "retryable": False},
        )
    if reply == "nonterminal":
        result["status"] = "running"
    if reply == "bad_result":
        result["result"] = []
    if reply == "bad_error":
        result.update(status="failed", error={"code": 1})
    response = opener.return_value.open.return_value.__enter__.return_value
    response.status = 202 if reply == "async" else 200
    response.read.return_value = json.dumps(result).encode()
    if reply == "non_json":
        response.read.return_value = b"not json"
    if reply == "non_object":
        response.read.return_value = b"[]"
    if reply == "invalid_utf8":
        response.read.return_value = b"\xff"
    if reply == "nonfinite":
        response.read.return_value = b'{"value": NaN}'
    if reply == "oversized":
        response.read.return_value = b"x" * (1024 * 1024 + 1)
    if reply == "timeout":
        opener.return_value.open.side_effect = TimeoutError()
    if reply == "http_error":
        opener.return_value.open.side_effect = HTTPError(
            "http://private/tasks", 503, "private-message", Message(), None
        )
    caplog.set_level(logging.INFO, logger="workflow_planner")
    actual = _send_task("http://kio7:8000/tasks", None, payload)
    expected = (
        "succeeded"
        if reply == "valid"
        else "failed" if reply == "failure" else "unknown"
    )
    assert actual["status"] == expected
    opener.return_value.open.assert_called_once()
    assert f"status={expected} duration_ms=" in caplog.text
    assert "request_bytes=" in caplog.text and "response_bytes=" in caplog.text
    if reply == "timeout":
        assert (
            "phase=connect" in caplog.text and "error_type=TimeoutError" in caplog.text
        )
    if reply == "non_json":
        assert "phase=parse_response" in caplog.text
    if reply == "mismatch":
        assert "phase=validate_response" in caplog.text
    if reply == "http_error":
        assert "http_status=503" in caplog.text and "phase=http_status" in caplog.text
        assert (
            "private-message" not in caplog.text
            and "http://private/tasks" not in caplog.text
        )


@patch("workflow_planner.urllib.request.build_opener")
def test_terminal_payload_matches_agent_request_body(
    opener: MagicMock,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Print the actual task body, including dependency results, without auth headers."""
    payload = {
        "schema_version": "1.0",
        "workflow_id": "wf-test",
        "step_id": "s2",
        "agent_id": "KIO11",
        "idempotency_key": "abc",
        "capability": "validation",
        "task": "Validate service",
        "data": {"checks": ["CRUD"]},
        "dependency_results": {"s1": {"artifact": "service.zip"}},
        "deadline_at": "2026-10-30T12:01:00+00:00",
    }
    monkeypatch.setenv("KIO_TEST_PRINT_TOKEN", "header-only-test-secret")
    response = opener.return_value.open.return_value.__enter__.return_value
    response.status = 200
    response.read.return_value = json.dumps(
        {**payload, "status": "succeeded", "result": {}}
    ).encode()
    assert (
        _send_task("http://kio11:8000/tasks", "KIO_TEST_PRINT_TOKEN", payload)["status"]
        == "succeeded"
    )
    output = capsys.readouterr().out
    printed_json = output.split(
        "--- KIO1 agent payload JSON (workflow wf-test, step s2, agent KIO11) ---\n", 1
    )[1].split("\n--- end agent payload JSON ---", 1)[0]
    request = opener.return_value.open.call_args.args[0]
    assert json.loads(printed_json) == json.loads(request.data) == payload
    assert "header-only-test-secret" not in output
    assert "Authorization" not in output


def test_invalid_plan_type() -> None:
    """Reject nonobject plans without incidental exceptions."""
    with pytest.raises(ValueError):
        validate_plan([])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "bad id"),
        ("workflow_id", 1),
        ("execution_mode", "unsupported"),
        ("steps", []),
        ("steps", {}),
        ("steps", [None]),
        ("steps", [{}] * 65),
    ],
)
def test_invalid_plan_fields(field: str, value: object) -> None:
    """Reject malformed top-level inputs before dispatch."""
    plan = make_plan()
    plan[field] = value
    with pytest.raises(ValueError):
        validate_plan(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("step_id", None),
        ("agent_id", "KIO1"),
        ("capability", []),
        ("task", " "),
        ("task", "x" * 16001),
        ("depends_on", "s1"),
        ("depends_on", [1]),
        ("data", []),
    ],
)
def test_invalid_step_fields(field: str, value: object) -> None:
    """Reject malformed task inputs before contacting any agent."""
    plan = make_plan()
    plan["steps"][0][field] = value
    with pytest.raises(ValueError):
        validate_plan(plan)


def test_parallel_plan_rejects_dependencies() -> None:
    """Do not accept dependency ordering in an independent parallel plan."""
    plan = make_plan("parallel")
    plan["steps"][1]["depends_on"] = ["s1"]
    with pytest.raises(ValueError, match="independent"):
        validate_plan(plan)


def test_oversized_plan_is_rejected() -> None:
    """Reject aggregate plan size even when its task text is short."""
    plan = make_plan()
    plan["steps"][1]["data"] = {"large": "x" * (1024 * 1024)}
    with pytest.raises(ValueError, match="1 MiB"):
        validate_plan(plan)


@pytest.mark.parametrize(
    "registry",
    [
        [],
        {},
        {"KIO99": {}},
        {"KIO7": []},
        {"KIO7": {}},
        {"KIO7": {"endpoint": "http://"}},
        {"KIO7": {"endpoint": "http://user:password@agent/tasks"}},
        {"KIO7": {"endpoint": "http://agent/tasks?query=1"}},
        {"KIO7": {"endpoint": "http://agent/tasks#fragment"}},
        {"KIO7": {"endpoint": "http://agent/tasks", "token_env": []}},
        {
            "KIO7": {
                "endpoint": "http://agent/tasks",
                "token_env": "KIO_MISSING_TEST_TOKEN",
            }
        },
    ],
)
def test_invalid_registry_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: object
) -> None:
    """Reject malformed routes and credentials before executing tasks."""
    monkeypatch.delenv("KIO_MISSING_TEST_TOKEN", raising=False)
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(registry))
    with pytest.raises(WorkflowConfigurationError):
        load_agent_registry(path)


def test_redirect_handler_returns_no_replacement_request() -> None:
    """Explicitly refuse redirect requests without leaking task credentials."""
    with patch("workflow_planner.urllib.request.Request") as replacement:
        _NoRedirect().redirect_request(
            MagicMock(), None, 302, "Found", {}, "http://other/tasks"
        )
        replacement.assert_not_called()


@patch("workflow_planner._send_task")
def test_nonfinite_data_rejected_before_dispatch(sender: MagicMock) -> None:
    """A malformed later input must not allow earlier tasks to start."""
    plan = make_plan()
    plan["steps"][1]["data"] = {"invalid": float("nan")}
    with pytest.raises(ValueError):
        dispatch_workflow(plan, REGISTRY)
    sender.assert_not_called()


@patch("workflow_planner.urllib.request.build_opener")
def test_broken_http_response_is_unknown(opener: MagicMock) -> None:
    """Broken responses cannot establish whether the container executed work."""
    opener.return_value.open.side_effect = http.client.IncompleteRead(b"partial")
    payload = {
        "workflow_id": "wf-test",
        "step_id": "s1",
        "agent_id": "KIO7",
        "idempotency_key": "abc",
    }
    assert _send_task("http://kio7:8000/tasks", None, payload)["status"] == "unknown"


@patch("workflow_planner.urllib.request.build_opener")
def test_oversized_task_is_not_sent(opener: MagicMock) -> None:
    """Oversized dependency inputs fail locally without a network request."""
    payload = {
        "workflow_id": "wf-test",
        "step_id": "s1",
        "data": {"large": "x" * (1024 * 1024)},
    }
    result = _send_task("http://kio7:8000/tasks", None, payload)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "INPUT_TOO_LARGE"
    opener.assert_not_called()


def test_parallel_tasks_actually_overlap() -> None:
    """Both ready tasks must start before either is allowed to complete."""
    barrier = threading.Barrier(2)

    def send_together(endpoint: str, token_env: str | None, payload: dict) -> dict:
        """Coordinate workers to detect accidentally serialized dispatch."""
        barrier.wait(timeout=3)
        return succeeded(endpoint, token_env, payload)

    with patch("workflow_planner._send_task", side_effect=send_together):
        assert (
            dispatch_workflow(make_plan("parallel"), REGISTRY)["status"] == "succeeded"
        )


@pytest.mark.parametrize("redirect", [False, True])
def test_real_http_contract_and_redirect_rejection(
    monkeypatch: pytest.MonkeyPatch, redirect: bool
) -> None:
    """Exercise actual urllib transport against disposable local HTTP agents."""
    received: list[dict] = []

    class AgentHandler(BaseHTTPRequestHandler):
        """Minimal synchronous receiver implementing the documented contract."""

        def do_POST(self) -> None:
            """Capture task/auth headers and return a correlated terminal result."""
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(payload)
            assert self.headers["Idempotency-Key"] == payload["idempotency_key"]
            assert self.headers["Authorization"] == "Bearer synthetic-test-token"
            if redirect:
                self.send_response(302)
                self.send_header("Location", "/tasks")
                self.end_headers()
                return
            response = json.dumps(
                {**payload, "status": "succeeded", "result": {"ok": True}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            """Suppress routine test-server access logs."""

    monkeypatch.setenv("KIO_TEST_TOKEN", "synthetic-test-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), AgentHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/tasks"
        registry = {
            agent_id: {"endpoint": endpoint, "token_env": "KIO_TEST_TOKEN"}
            for agent_id in REGISTRY
        }
        result = dispatch_workflow(make_plan(), registry)
        if redirect:
            assert result["status"] == "unknown"
            assert len(received) == 1
            assert result["steps"][1]["status"] == "blocked"
        else:
            assert result["status"] == "succeeded"
            assert len(received) == 2
            assert received[1]["dependency_results"] == {"s1": {"ok": True}}
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()
