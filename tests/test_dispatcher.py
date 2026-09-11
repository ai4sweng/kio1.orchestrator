import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from config_loader import DispatchSettings, load_config
from kio10.dispatcher import (
    DispatchReport,
    StepResult,
    create_transport,
    dispatch_plan,
    format_report,
    run_workflow,
    write_report,
)
from kio10.stub import StubKIO10
from kio10.transport import (
    SCHEMA_VERSION,
    HttpKIO10,
    TransportError,
    build_request,
    run_job,
)
from workflow_plan import Step, WorkflowPlan

# ----------------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------------


def make_plan(*steps: Step, execution_mode: str = "mixed") -> WorkflowPlan:
    return WorkflowPlan(
        workflow_id="wf-test01",
        execution_mode=execution_mode,
        steps=steps,
        explanation="test",
    )


def make_step(
    step_id: str = "s7",
    depends_on: tuple[str, ...] = (),
    agent_id: str = "KIO10",
    task: str | None = None,
    capability: str = "energy_efficiency",
) -> Step:
    return Step(
        step_id=step_id,
        agent_id=agent_id,
        capability=capability,
        task=task if task is not None else f"task {step_id}",
        depends_on=depends_on,
    )


def make_settings(
    agents: dict[str, str] | None = None,
    step_timeout: float = 5.0,
    max_parallel_steps: int = 4,
) -> DispatchSettings:
    return DispatchSettings(
        enabled=True,
        poll_interval_seconds=0,
        step_timeout_seconds=step_timeout,
        max_parallel_steps=max_parallel_steps,
        agents=agents if agents is not None else {"KIO10": "stub://"},
    )


class RecordingStub(StubKIO10):
    """Stub that records every submit and poll so tests can assert ordering."""

    def __init__(self, polls_before_done: int = 1) -> None:
        super().__init__(polls_before_done=polls_before_done)
        self.events: list[tuple[str, str]] = []
        self.requests: dict[str, dict[str, Any]] = {}

    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        self.events.append(("submit", request["step_id"]))
        self.requests[request["step_id"]] = request
        return await super().submit(request)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        reply = await super().get_job(job_id)
        self.events.append(("poll", reply["step_id"]))
        return reply


def run(
    plan: WorkflowPlan, settings: DispatchSettings, **kwargs: Any
) -> DispatchReport:
    return asyncio.run(run_workflow(plan, settings, **kwargs))


def by_id(report: DispatchReport) -> dict[str, StepResult]:
    return {result.step_id: result for result in report.results}


# ----------------------------------------------------------------------------
# Dispatch settings in config.json
# ----------------------------------------------------------------------------


def write_config(tmp_path: Path, dispatch: dict[str, Any] | None) -> Path:
    data: dict[str, Any] = {
        "provider": "ollama",
        "allowed_providers": ["ollama"],
        "model": "m",
        "prompt_path": "p.txt",
        "chat_directory": "chats",
        "temperature": 0.1,
        "request_timeout": 10,
        "provider_options": {
            "endpoint": "http://localhost:11434",
            "context_window_size": 8192,
        },
    }
    if dispatch is not None:
        data["dispatch"] = dispatch
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_dispatch_is_disabled_by_default_when_section_is_absent(tmp_path: Path) -> None:
    config = load_config(str(write_config(tmp_path, None)))

    assert config.dispatch == DispatchSettings()
    assert config.dispatch.enabled is False
    assert config.dispatch.agents == {}


def test_dispatch_section_is_parsed(tmp_path: Path) -> None:
    config = load_config(
        str(
            write_config(
                tmp_path,
                {
                    "enabled": True,
                    "poll_interval_seconds": 0.5,
                    "step_timeout_seconds": 30,
                    "agents": {"KIO10": "http://localhost:8010", "KIO7": "stub://"},
                },
            )
        )
    )

    assert config.dispatch.enabled is True
    assert config.dispatch.poll_interval_seconds == 0.5
    assert config.dispatch.step_timeout_seconds == 30
    assert config.dispatch.agents == {
        "KIO10": "http://localhost:8010",
        "KIO7": "stub://",
    }


def test_dispatch_section_fields_have_defaults(tmp_path: Path) -> None:
    config = load_config(str(write_config(tmp_path, {"enabled": True})))

    assert (
        config.dispatch.poll_interval_seconds
        == DispatchSettings().poll_interval_seconds
    )
    assert (
        config.dispatch.step_timeout_seconds == DispatchSettings().step_timeout_seconds
    )
    assert config.dispatch.max_parallel_steps == DispatchSettings().max_parallel_steps
    assert DispatchSettings().max_parallel_steps == 4
    assert config.dispatch.agents == {}


def test_dispatch_max_parallel_steps_is_parsed(tmp_path: Path) -> None:
    config = load_config(str(write_config(tmp_path, {"max_parallel_steps": 2})))

    assert config.dispatch.max_parallel_steps == 2


@pytest.mark.parametrize(
    "dispatch, message",
    [
        ({"enabled": "yes"}, "enabled"),
        ({"poll_interval_seconds": 0}, "poll_interval_seconds"),
        ({"poll_interval_seconds": -1}, "poll_interval_seconds"),
        ({"step_timeout_seconds": 0}, "step_timeout_seconds"),
        ({"max_parallel_steps": 0}, "max_parallel_steps"),
        ({"max_parallel_steps": -1}, "max_parallel_steps"),
        ({"max_parallel_steps": 2.5}, "max_parallel_steps"),
        ({"max_parallel_steps": True}, "max_parallel_steps"),
        ({"max_parallel_steps": "4"}, "max_parallel_steps"),
        ({"agents": ["KIO10"]}, "agents"),
        ({"agents": {"KIO10": 8010}}, "agents"),
        ({"agents": {"KIO10": "amqp://broker"}}, "amqp"),
        ("on", "dispatch"),
    ],
)
def test_invalid_dispatch_section_is_rejected(
    tmp_path: Path, dispatch: Any, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(str(write_config(tmp_path, dispatch)))


# ----------------------------------------------------------------------------
# KIO10 stub agent
# ----------------------------------------------------------------------------


def make_request(step_id: str = "s7", task: str = "assess energy") -> dict[str, Any]:
    """Build a KIO1 -> KIO10 request in the documented format."""
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": "wf-test01",
        "step_id": step_id,
        "capability": "energy_efficiency",
        "task": task,
        "data": {},
    }


def test_submit_returns_documented_acknowledgement() -> None:
    stub = StubKIO10()

    ack = asyncio.run(stub.submit(make_request()))

    assert ack["schema_version"] == "1.0"
    assert ack["workflow_id"] == "wf-test01"
    assert ack["step_id"] == "s7"
    assert ack["status"] == "accepted"
    assert isinstance(ack["job_id"], str) and ack["job_id"]


def test_job_reports_accepted_until_configured_polls_then_success() -> None:
    stub = StubKIO10(polls_before_done=2)

    async def scenario() -> list[str]:
        ack = await stub.submit(make_request())
        statuses = []
        for _ in range(4):
            reply = await stub.get_job(ack["job_id"])
            statuses.append(reply["status"])
        return statuses

    assert asyncio.run(scenario()) == ["accepted", "accepted", "success", "success"]


def test_success_reply_carries_artifacts_with_receipts() -> None:
    stub = StubKIO10(polls_before_done=0)

    async def scenario() -> dict[str, Any]:
        ack = await stub.submit(make_request())
        return await stub.get_job(ack["job_id"])

    reply = asyncio.run(scenario())

    assert reply["workflow_id"] == "wf-test01"
    assert reply["step_id"] == "s7"
    assert reply["status"] == "success"
    artifacts = reply["artifacts"]
    assert artifacts, "success reply must carry at least one artifact"
    for artifact in artifacts.values():
        assert artifact["schema_id"]
        receipt = artifact["receipt"]
        assert receipt["uri"].startswith("shm://artifacts/wf-test01/s7/")
        assert receipt["version"] == 1
        assert receipt["content_hash"].startswith("sha256:")


def test_task_marker_produces_failure_reply() -> None:
    stub = StubKIO10(polls_before_done=0)

    async def scenario() -> dict[str, Any]:
        ack = await stub.submit(make_request(task="build firmware [stub:fail]"))
        return await stub.get_job(ack["job_id"])

    reply = asyncio.run(scenario())

    assert reply["status"] == "failure"
    assert reply["failure_class"]
    assert reply["diagnostics"]["receipt"]["uri"].startswith("shm://")


def test_task_marker_produces_needs_clarification_reply() -> None:
    stub = StubKIO10(polls_before_done=0)

    async def scenario() -> dict[str, Any]:
        ack = await stub.submit(make_request(task="assess energy [stub:clarify]"))
        return await stub.get_job(ack["job_id"])

    reply = asyncio.run(scenario())

    assert reply["status"] == "needs_clarification"
    assert reply["clarification"]["reason"]
    assert len(reply["clarification"]["options"]) >= 2


def test_resubmitting_same_step_returns_existing_job() -> None:
    stub = StubKIO10(polls_before_done=1)

    async def scenario() -> tuple[str, str, str]:
        first = await stub.submit(make_request())
        await stub.get_job(first["job_id"])
        second = await stub.submit(make_request())
        reply = await stub.get_job(second["job_id"])
        return first["job_id"], second["job_id"], reply["status"]

    first_id, second_id, status = asyncio.run(scenario())

    assert first_id == second_id
    assert status == "success", "poll count must not reset on resubmission"


def test_different_steps_get_different_jobs() -> None:
    stub = StubKIO10()

    async def scenario() -> tuple[str, str]:
        a = await stub.submit(make_request(step_id="s1"))
        b = await stub.submit(make_request(step_id="s2"))
        return a["job_id"], b["job_id"]

    a, b = asyncio.run(scenario())

    assert a != b


def test_unknown_job_id_is_rejected() -> None:
    stub = StubKIO10()

    with pytest.raises(KeyError):
        asyncio.run(stub.get_job("kio10-job-9999"))


# ----------------------------------------------------------------------------
# KIO1 <-> KIO10 transport and contract
# ----------------------------------------------------------------------------


def test_build_request_matches_documented_format() -> None:
    data = {
        "code_ref": {
            "uri": "shm://artifacts/wf-medsch01/s3/codebase/v1",
            "schema_id": "code_bundle/1.0",
        }
    }

    request = build_request(
        "wf-medsch01",
        make_step(capability="energy_efficiency_analysis", task="assess energy"),
        data,
    )

    assert request == {
        "schema_version": "1.0",
        "workflow_id": "wf-medsch01",
        "step_id": "s7",
        "capability": "energy_efficiency_analysis",
        "task": "assess energy",
        "data": data,
    }


def test_run_job_polls_until_final_reply() -> None:
    stub = StubKIO10(polls_before_done=2)
    request = build_request("wf-test01", make_step(), {})

    reply = asyncio.run(run_job(stub, request, poll_interval=0))

    assert reply["status"] == "success"
    assert reply["step_id"] == "s7"


def test_run_job_returns_failure_and_needs_clarification_as_final() -> None:
    stub = StubKIO10(polls_before_done=0)

    fail = asyncio.run(
        run_job(stub, build_request("wf", make_step(task="x [stub:fail]"), {}), 0)
    )
    clarify = asyncio.run(
        run_job(
            stub,
            build_request("wf", make_step(step_id="s8", task="x [stub:clarify]"), {}),
            0,
        )
    )

    assert fail["status"] == "failure"
    assert clarify["status"] == "needs_clarification"


class _MisbehavingAgent:
    """Transport double whose replies violate the contract in one chosen way."""

    def __init__(self, ack: dict[str, Any], reply: dict[str, Any]) -> None:
        self._ack = ack
        self._reply = reply

    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._ack

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return self._reply

    async def aclose(self) -> None:
        pass


def _ack(**overrides: Any) -> dict[str, Any]:
    ack = {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": "wf-test01",
        "step_id": "s7",
        "job_id": "kio10-job-1",
        "status": "accepted",
    }
    ack.update(overrides)
    return ack


def test_run_job_rejects_acknowledgement_without_job_id() -> None:
    agent = _MisbehavingAgent(_ack(job_id=None), _ack(status="success"))
    request = build_request("wf-test01", make_step(), {})

    with pytest.raises(TransportError, match="job_id"):
        asyncio.run(run_job(agent, request, poll_interval=0))


def test_run_job_rejects_acknowledgement_with_unexpected_status() -> None:
    agent = _MisbehavingAgent(_ack(status="queued"), _ack(status="success"))
    request = build_request("wf-test01", make_step(), {})

    with pytest.raises(TransportError, match="queued"):
        asyncio.run(run_job(agent, request, poll_interval=0))


def test_run_job_rejects_reply_for_a_different_step() -> None:
    agent = _MisbehavingAgent(_ack(), _ack(status="success", step_id="s9"))
    request = build_request("wf-test01", make_step(), {})

    with pytest.raises(TransportError, match="s9"):
        asyncio.run(run_job(agent, request, poll_interval=0))


@pytest.mark.parametrize("schema_version", ["2.0", "", None])
def test_run_job_rejects_acknowledgement_with_wrong_schema_version(
    schema_version: Any,
) -> None:
    ack = _ack()
    ack["schema_version"] = schema_version
    agent = _MisbehavingAgent(ack, _ack(status="success"))
    request = build_request("wf-test01", make_step(), {})

    with pytest.raises(TransportError, match="schema_version"):
        asyncio.run(run_job(agent, request, poll_interval=0))


@pytest.mark.parametrize("schema_version", ["2.0", "", None])
def test_run_job_rejects_final_reply_with_wrong_schema_version(
    schema_version: Any,
) -> None:
    reply = _ack(status="success")
    reply["schema_version"] = schema_version
    agent = _MisbehavingAgent(_ack(), reply)
    request = build_request("wf-test01", make_step(), {})

    with pytest.raises(TransportError, match="schema_version"):
        asyncio.run(run_job(agent, request, poll_interval=0))


def test_run_job_rejects_reply_without_schema_version() -> None:
    reply = _ack(status="success")
    del reply["schema_version"]
    agent = _MisbehavingAgent(_ack(), reply)
    request = build_request("wf-test01", make_step(), {})

    with pytest.raises(TransportError, match="schema_version"):
        asyncio.run(run_job(agent, request, poll_interval=0))


class _ScriptedAgent:
    """Transport double replying with a fixed final body for every job."""

    def __init__(self, final: dict[str, Any]) -> None:
        self._final = final
        self._envelope: dict[str, Any] = {}

    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        self._envelope = {
            "workflow_id": request["workflow_id"],
            "step_id": request["step_id"],
        }
        return {**_ack(), **self._envelope}

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return {**_ack(), **self._envelope, **self._final}

    async def aclose(self) -> None:
        pass


@pytest.mark.parametrize(
    "final",
    [
        {"status": "success", "artifacts": [{"name": "x"}]},
        {"status": "success", "artifacts": {"x": "shm://artifacts/a"}},
        {
            "status": "success",
            "artifacts": {"x": {"schema_id": "s/1.0", "receipt": "shm://a"}},
        },
        {
            "status": "success",
            "artifacts": {"x": {"schema_id": "s/1.0", "receipt": {"uri": 7}}},
        },
        {"status": "needs_clarification", "clarification": "just text"},
        {"status": "failure", "failure_class": ["not", "a", "string"]},
    ],
    ids=[
        "artifacts-list",
        "artifact-str",
        "receipt-str",
        "uri-int",
        "clarification-str",
        "failure_class-list",
    ],
)
def test_malformed_final_reply_body_is_reported_as_error_not_crash(
    final: dict[str, Any],
) -> None:
    plan = make_plan(make_step("s1"), make_step("s2", ("s1",)))

    results = by_id(
        run(plan, make_settings(), transports={"KIO10": _ScriptedAgent(final)})
    )

    assert results["s1"].status == "error"
    assert "malformed" in results["s1"].detail
    assert results["s2"].status == "skipped"


def test_success_reply_without_artifacts_field_is_accepted() -> None:
    plan = make_plan(make_step("s1"), make_step("s2", ("s1",)))
    agent = _ScriptedAgent({"status": "success"})

    results = by_id(run(plan, make_settings(), transports={"KIO10": agent}))

    assert results["s1"].status == "success"
    assert results["s1"].detail == "no artifacts"
    assert results["s2"].status == "success"


def test_run_job_rejects_poll_reply_for_a_different_job() -> None:
    agent = _MisbehavingAgent(
        _ack(job_id="kio10-job-0042"),
        _ack(status="success", job_id="kio10-job-0017"),
    )
    request = build_request("wf-test01", make_step(), {})

    with pytest.raises(TransportError, match="kio10-job-0017"):
        asyncio.run(run_job(agent, request, poll_interval=0))


def test_run_job_rejects_poll_reply_without_job_id() -> None:
    reply = _ack(status="success")
    del reply["job_id"]
    agent = _MisbehavingAgent(_ack(), reply)
    request = build_request("wf-test01", make_step(), {})

    with pytest.raises(TransportError, match="job_id"):
        asyncio.run(run_job(agent, request, poll_interval=0))


@pytest.mark.parametrize(
    "job_id", ["../admin", "jobs/0042", "0042?status=success", "job 42", "job\n42"]
)
def test_run_job_rejects_job_id_that_is_not_a_plain_identifier(job_id: str) -> None:
    agent = _MisbehavingAgent(
        _ack(job_id=job_id), _ack(status="success", job_id=job_id)
    )
    request = build_request("wf-test01", make_step(), {})

    with pytest.raises(TransportError, match="job_id"):
        asyncio.run(run_job(agent, request, poll_interval=0))


def test_run_job_accepts_job_ids_with_letters_digits_and_separators() -> None:
    job_id = "kio10-job_0042.v1:a"
    agent = _MisbehavingAgent(
        _ack(job_id=job_id), _ack(status="success", job_id=job_id)
    )
    request = build_request("wf-test01", make_step(), {})

    reply = asyncio.run(run_job(agent, request, poll_interval=0))

    assert reply["job_id"] == job_id


def test_http_transport_encodes_job_id_as_a_single_path_segment() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={})

    transport = HttpKIO10(
        "http://kio10.local", timeout=5.0, httpx_transport=httpx2.MockTransport(handler)
    )

    asyncio.run(transport.get_job("../admin?x=1 y"))

    assert str(seen[0].url) == "http://kio10.local/jobs/..%2Fadmin%3Fx%3D1%20y"


def test_run_job_rejects_unknown_final_status() -> None:
    agent = _MisbehavingAgent(_ack(), _ack(status="done"))
    request = build_request("wf-test01", make_step(), {})

    with pytest.raises(TransportError, match="done"):
        asyncio.run(run_job(agent, request, poll_interval=0))


def test_create_transport_selects_stub_for_stub_scheme() -> None:
    transport = create_transport("stub://", timeout=5.0)

    assert isinstance(transport, StubKIO10)


def test_create_transport_selects_http_for_http_scheme() -> None:
    transport = create_transport("http://localhost:8010", timeout=5.0)

    assert isinstance(transport, HttpKIO10)


def test_create_transport_rejects_unknown_scheme() -> None:
    with pytest.raises(ValueError, match="amqp"):
        create_transport("amqp://broker", timeout=5.0)


def _fake_kio10_server() -> tuple[httpx2.MockTransport, list[httpx2.Request]]:
    """Serve the documented KIO10 endpoints from memory."""
    seen: list[httpx2.Request] = []
    polls = {"count": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.method == "POST" and request.url.path == "/jobs":
            body = json.loads(request.content)
            return httpx2.Response(
                202,
                json={
                    "schema_version": "1.0",
                    "workflow_id": body["workflow_id"],
                    "step_id": body["step_id"],
                    "job_id": "kio10-job-0042",
                    "status": "accepted",
                },
            )
        if request.method == "GET" and request.url.path == "/jobs/kio10-job-0042":
            polls["count"] += 1
            status = "accepted" if polls["count"] < 2 else "success"
            payload: dict[str, Any] = {
                "schema_version": "1.0",
                "workflow_id": "wf-test01",
                "step_id": "s7",
                "job_id": "kio10-job-0042",
                "status": status,
            }
            if status == "success":
                payload["artifacts"] = {}
            return httpx2.Response(200, json=payload)
        return httpx2.Response(404)

    return httpx2.MockTransport(handler), seen


def test_http_transport_posts_request_and_polls_job_endpoint() -> None:
    mock, seen = _fake_kio10_server()
    transport = HttpKIO10("http://kio10.local", timeout=5.0, httpx_transport=mock)
    request = build_request("wf-test01", make_step(), {})

    reply = asyncio.run(run_job(transport, request, poll_interval=0))

    assert reply["status"] == "success"
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "http://kio10.local/jobs"
    assert json.loads(seen[0].content) == request
    assert [r.method for r in seen[1:]] == ["GET", "GET"]
    assert str(seen[1].url) == "http://kio10.local/jobs/kio10-job-0042"


def _count_async_clients(monkeypatch: pytest.MonkeyPatch) -> list[httpx2.AsyncClient]:
    """Record every AsyncClient the transport constructs."""
    created: list[httpx2.AsyncClient] = []
    real_client = httpx2.AsyncClient

    def counting_client(*args: Any, **kwargs: Any) -> httpx2.AsyncClient:
        client = real_client(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(httpx2, "AsyncClient", counting_client)
    return created


def test_http_transport_reuses_one_client_across_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _count_async_clients(monkeypatch)
    mock, _ = _fake_kio10_server()
    transport = HttpKIO10("http://kio10.local", timeout=5.0, httpx_transport=mock)
    request = build_request("wf-test01", make_step(), {})

    asyncio.run(run_job(transport, request, poll_interval=0))

    assert len(created) == 1, "submit and every poll must share one AsyncClient"


def test_http_transport_refuses_use_from_another_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _count_async_clients(monkeypatch)
    mock, _ = _fake_kio10_server()
    transport = HttpKIO10("http://kio10.local", timeout=5.0, httpx_transport=mock)

    asyncio.run(transport.get_job("kio10-job-0042"))
    with pytest.raises(TransportError, match="event loop"):
        asyncio.run(transport.get_job("kio10-job-0042"))

    assert len(created) == 1, "no second client may be opened for another loop"


def test_http_transport_can_be_closed_from_another_event_loop() -> None:
    mock, _ = _fake_kio10_server()
    transport = HttpKIO10("http://kio10.local", timeout=5.0, httpx_transport=mock)

    asyncio.run(transport.get_job("kio10-job-0042"))
    asyncio.run(transport.aclose())

    with pytest.raises(TransportError, match="closed"):
        asyncio.run(transport.get_job("kio10-job-0042"))


def test_http_transport_rejects_use_after_aclose() -> None:
    mock, _ = _fake_kio10_server()
    transport = HttpKIO10("http://kio10.local", timeout=5.0, httpx_transport=mock)

    async def scenario() -> None:
        await transport.get_job("kio10-job-0042")
        await transport.aclose()
        await transport.aclose()
        with pytest.raises(TransportError, match="closed"):
            await transport.get_job("kio10-job-0042")

    asyncio.run(scenario())


def test_http_transport_aclose_without_requests_is_a_no_op() -> None:
    transport = HttpKIO10("http://kio10.local", timeout=5.0)

    asyncio.run(transport.aclose())


class _ClosableStub(StubKIO10):
    def __init__(self) -> None:
        super().__init__(polls_before_done=0)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def test_run_workflow_closes_transports_it_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_ClosableStub] = []

    def fake_create_transport(address: str, timeout: float) -> _ClosableStub:
        stub = _ClosableStub()
        created.append(stub)
        return stub

    monkeypatch.setattr("kio10.dispatcher.create_transport", fake_create_transport)
    plan = make_plan(make_step("s1"))

    report = run(plan, make_settings(agents={"KIO10": "stub://"}))

    assert report.results[0].status == "success"
    assert [stub.closed for stub in created] == [True]


def test_run_workflow_closes_every_owned_transport_even_if_one_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_ClosableStub] = []

    class _FailingCloseStub(_ClosableStub):
        async def aclose(self) -> None:
            self.closed = True
            raise RuntimeError("pool shutdown failed")

    def fake_create_transport(address: str, timeout: float) -> _ClosableStub:
        stub = _FailingCloseStub() if not created else _ClosableStub()
        created.append(stub)
        return stub

    monkeypatch.setattr("kio10.dispatcher.create_transport", fake_create_transport)
    plan = make_plan(make_step("s1"), make_step("s2", agent_id="KIO10b"))

    report = run(plan, make_settings(agents={"KIO10": "stub://", "KIO10b": "stub://"}))

    assert [r.status for r in report.results] == ["success", "success"]
    assert [stub.closed for stub in created] == [True, True]


def test_run_workflow_leaves_injected_transports_open() -> None:
    stub = _ClosableStub()
    plan = make_plan(make_step("s1"))

    run(plan, make_settings(), transports={"KIO10": stub})

    assert stub.closed is False


def test_http_transport_wraps_connection_errors() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    transport = HttpKIO10(
        "http://kio10.local", timeout=5.0, httpx_transport=httpx2.MockTransport(handler)
    )

    with pytest.raises(TransportError, match="connection refused"):
        asyncio.run(transport.submit(build_request("wf", make_step(), {})))


def test_http_transport_wraps_http_error_status() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, text="boom")

    transport = HttpKIO10(
        "http://kio10.local", timeout=5.0, httpx_transport=httpx2.MockTransport(handler)
    )

    with pytest.raises(TransportError, match="500"):
        asyncio.run(transport.submit(build_request("wf", make_step(), {})))


def test_http_transport_wraps_non_json_body() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text="<html>not json</html>")

    transport = HttpKIO10(
        "http://kio10.local", timeout=5.0, httpx_transport=httpx2.MockTransport(handler)
    )

    with pytest.raises(TransportError, match="JSON"):
        asyncio.run(transport.get_job("kio10-job-1"))


# ----------------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------------


def test_all_steps_succeed_on_stub_and_results_follow_plan_order() -> None:
    plan = make_plan(
        make_step("s1"), make_step("s2", ("s1",)), make_step("s3", ("s2",))
    )

    report = run(plan, make_settings())

    assert report.workflow_id == "wf-test01"
    assert [r.step_id for r in report.results] == ["s1", "s2", "s3"]
    assert all(r.status == "success" for r in report.results)
    assert all(r.job_id for r in report.results)
    assert all(r.duration_ms >= 0 for r in report.results)
    assert all(r.agent_id == "KIO10" for r in report.results)


def test_dependent_step_waits_for_dependency_to_finish() -> None:
    stub = RecordingStub(polls_before_done=1)
    plan = make_plan(make_step("s1"), make_step("s2", ("s1",)))

    run(plan, make_settings(), transports={"KIO10": stub})

    assert stub.events == [
        ("submit", "s1"),
        ("poll", "s1"),
        ("poll", "s1"),
        ("submit", "s2"),
        ("poll", "s2"),
        ("poll", "s2"),
    ]


def test_independent_steps_are_submitted_before_any_finishes() -> None:
    stub = RecordingStub(polls_before_done=1)
    plan = make_plan(make_step("s1"), make_step("s2"), make_step("s3", ("s1", "s2")))

    run(plan, make_settings(), transports={"KIO10": stub})

    submits = [e for e in stub.events if e[0] == "submit"]
    s1_finished_index = len(stub.events) - 1 - stub.events[::-1].index(("poll", "s1"))
    assert stub.events.index(("submit", "s1")) < s1_finished_index
    assert stub.events.index(("submit", "s2")) < s1_finished_index
    assert submits[-1] == ("submit", "s3")


def test_max_parallel_steps_limits_how_many_steps_run_at_once() -> None:
    stub = RecordingStub(polls_before_done=1)
    plan = make_plan(make_step("s1"), make_step("s2"), make_step("s3"))

    run(plan, make_settings(max_parallel_steps=1), transports={"KIO10": stub})

    assert stub.events == [
        ("submit", "s1"),
        ("poll", "s1"),
        ("poll", "s1"),
        ("submit", "s2"),
        ("poll", "s2"),
        ("poll", "s2"),
        ("submit", "s3"),
        ("poll", "s3"),
        ("poll", "s3"),
    ]


def test_max_parallel_steps_of_two_keeps_two_steps_in_flight() -> None:
    stub = RecordingStub(polls_before_done=1)
    plan = make_plan(make_step("s1"), make_step("s2"), make_step("s3"))

    run(plan, make_settings(max_parallel_steps=2), transports={"KIO10": stub})

    s1_done = len(stub.events) - 1 - stub.events[::-1].index(("poll", "s1"))
    assert stub.events.index(("submit", "s2")) < s1_done
    assert stub.events.index(("submit", "s3")) > s1_done


def test_concurrency_limit_does_not_deadlock_dependent_steps() -> None:
    # The dependent steps are listed first so they start before their
    # dependency: a slot held while waiting for s1 would block s1 forever.
    plan = make_plan(
        make_step("s2", ("s1",)), make_step("s3", ("s2",)), make_step("s1")
    )

    async def bounded() -> DispatchReport:
        return await asyncio.wait_for(
            run_workflow(plan, make_settings(max_parallel_steps=1)), timeout=2
        )

    report = asyncio.run(bounded())

    assert [r.status for r in report.results] == ["success", "success", "success"]


def test_skipped_steps_do_not_consume_a_concurrency_slot() -> None:
    stub = RecordingStub(polls_before_done=0)
    plan = make_plan(
        make_step("s1", agent_id="KIO7"),
        make_step("s2", agent_id="KIO7"),
        make_step("s3"),
    )

    results = by_id(
        run(plan, make_settings(max_parallel_steps=1), transports={"KIO10": stub})
    )

    assert results["s1"].status == "skipped"
    assert results["s2"].status == "skipped"
    assert results["s3"].status == "success"


def test_dependency_artifacts_are_passed_in_data_of_dependent_step() -> None:
    stub = RecordingStub(polls_before_done=0)
    plan = make_plan(make_step("s1"), make_step("s2", ("s1",)))

    run(plan, make_settings(), transports={"KIO10": stub})

    assert stub.requests["s1"]["data"] == {}
    data = stub.requests["s2"]["data"]
    assert data == {
        "energy_efficiency_result": {
            "uri": "shm://artifacts/wf-test01/s1/energy_efficiency_result/v1",
            "schema_id": "energy_efficiency_result/1.0",
        }
    }


def test_colliding_artifact_names_from_two_dependencies_are_prefixed() -> None:
    stub = RecordingStub(polls_before_done=0)
    plan = make_plan(make_step("s1"), make_step("s2"), make_step("s3", ("s1", "s2")))

    run(plan, make_settings(), transports={"KIO10": stub})

    data = stub.requests["s3"]["data"]
    assert set(data) == {"energy_efficiency_result", "s2.energy_efficiency_result"}
    assert data["energy_efficiency_result"]["uri"].endswith(
        "/s1/energy_efficiency_result/v1"
    )
    assert data["s2.energy_efficiency_result"]["uri"].endswith(
        "/s2/energy_efficiency_result/v1"
    )


def test_step_for_undeployed_agent_is_skipped_not_errored() -> None:
    plan = make_plan(make_step("s1", agent_id="KIO7"), make_step("s2"))

    results = by_id(run(plan, make_settings()))

    assert results["s1"].status == "skipped"
    assert "not deployed" in results["s1"].detail
    assert results["s1"].job_id is None
    assert results["s2"].status == "success"


def test_dependents_of_skipped_step_are_skipped() -> None:
    plan = make_plan(make_step("s1", agent_id="KIO7"), make_step("s2", ("s1",)))

    results = by_id(run(plan, make_settings()))

    assert results["s2"].status == "skipped"
    assert "s1" in results["s2"].detail


def test_failure_is_reported_and_blocks_dependents() -> None:
    plan = make_plan(
        make_step("s1", task="build [stub:fail]"),
        make_step("s2", ("s1",)),
        make_step("s3"),
    )

    results = by_id(run(plan, make_settings()))

    assert results["s1"].status == "failure"
    assert "stub_forced_failure" in results["s1"].detail
    assert results["s2"].status == "skipped"
    assert "s1" in results["s2"].detail
    assert results["s3"].status == "success"


def test_needs_clarification_is_reported_and_blocks_dependents() -> None:
    plan = make_plan(make_step("s1", task="x [stub:clarify]"), make_step("s2", ("s1",)))

    results = by_id(run(plan, make_settings()))

    assert results["s1"].status == "needs_clarification"
    assert "clarification" in results["s1"].detail
    assert results["s2"].status == "skipped"


class NeverFinishingAgent(StubKIO10):
    async def get_job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs[job_id]
        return {
            "schema_version": "1.0",
            "workflow_id": job.request["workflow_id"],
            "step_id": job.request["step_id"],
            "job_id": job_id,
            "status": "accepted",
        }


def test_step_timeout_is_reported_as_error() -> None:
    plan = make_plan(make_step("s1"), make_step("s2", ("s1",)))

    results = by_id(
        run(
            plan,
            make_settings(step_timeout=0.05),
            transports={"KIO10": NeverFinishingAgent()},
        )
    )

    assert results["s1"].status == "error"
    assert "timed out" in results["s1"].detail
    assert results["s2"].status == "skipped"


class BrokenAgent:
    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        raise TransportError("connection refused")

    async def get_job(self, job_id: str) -> dict[str, Any]:
        raise AssertionError("must not be called")

    async def aclose(self) -> None:
        pass


def test_transport_error_is_reported_as_error_with_reason() -> None:
    plan = make_plan(make_step("s1"))

    results = by_id(run(plan, make_settings(), transports={"KIO10": BrokenAgent()}))

    assert results["s1"].status == "error"
    assert "connection refused" in results["s1"].detail


def test_error_in_one_step_does_not_stop_independent_steps() -> None:
    # Give s2 a different, working agent so the two steps are truly independent.
    plan = make_plan(make_step("s1"), make_step("s2", agent_id="KIO10b"))
    transports = {"KIO10": BrokenAgent(), "KIO10b": StubKIO10(polls_before_done=0)}

    results = by_id(
        run(
            plan,
            make_settings(agents={"KIO10": "stub://", "KIO10b": "stub://"}),
            transports=transports,
        )
    )

    assert results["s1"].status == "error"
    assert results["s2"].status == "success"


def test_transports_are_created_from_settings_addresses() -> None:
    plan = make_plan(make_step("s1"))

    results = by_id(run(plan, make_settings(agents={"KIO10": "stub://"})))

    assert results["s1"].status == "success"
    assert results["s1"].job_id is not None


def test_report_to_dict_is_json_serialisable_and_complete() -> None:
    plan = make_plan(make_step("s1"), make_step("s2", agent_id="KIO7"))

    report = run(plan, make_settings())
    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["workflow_id"] == "wf-test01"
    assert payload["summary"] == {"success": 1, "skipped": 1}
    assert [s["step_id"] for s in payload["steps"]] == ["s1", "s2"]
    assert set(payload["steps"][0]) >= {
        "step_id",
        "agent_id",
        "status",
        "job_id",
        "duration_ms",
        "detail",
    }


def test_write_report_stores_json_named_by_session_and_workflow(tmp_path: Path) -> None:
    plan = make_plan(make_step("s1"))
    report = run(plan, make_settings())

    path = write_report(report, str(tmp_path / "logs"), "20260904_100000_abcd1234")

    assert (
        path == tmp_path / "logs" / "dispatch_20260904_100000_abcd1234_wf-test01.json"
    )
    stored = json.loads(path.read_text())
    assert stored["workflow_id"] == "wf-test01"
    assert stored["steps"][0]["status"] == "success"


def test_format_report_lists_each_step_with_status_and_duration() -> None:
    plan = make_plan(make_step("s1"), make_step("s2", agent_id="KIO7"))
    report = run(plan, make_settings())

    text = format_report(report)

    lines = text.splitlines()
    assert any("s1" in line and "KIO10" in line and "success" in line for line in lines)
    assert any("s2" in line and "KIO7" in line and "skipped" in line for line in lines)
    assert "ms" in text
    assert "wf-test01" in text


def test_dispatch_plan_runs_plan_writes_report_and_returns_summary(
    tmp_path: Path,
) -> None:
    plan = make_plan(make_step("s1"), make_step("s2", ("s1",), agent_id="KIO8"))

    text = dispatch_plan(plan, make_settings(), str(tmp_path), "sess01")

    assert "success" in text and "skipped" in text
    assert (tmp_path / "dispatch_sess01_wf-test01.json").exists()


def test_step_data_is_sent_in_the_request() -> None:
    step = Step(
        step_id="s1",
        agent_id="KIO10",
        capability="tinyml",
        task="Train",
        data={
            "task_model": {
                "uri": "shm://demo/pim/v1",
                "schema_id": "task_model/1.0",
            }
        },
    )
    request = build_request("wf-data01", step, {})
    assert request["data"]["task_model"]["uri"] == "shm://demo/pim/v1"


def test_dependency_artifacts_join_step_data() -> None:
    step = Step(
        step_id="s2",
        agent_id="KIO10",
        capability="tinyml",
        task="Train",
        data={
            "task_model": {
                "uri": "shm://demo/pim/v1",
                "schema_id": "task_model/1.0",
            }
        },
    )
    from_deps = {
        "energy_report": {
            "uri": "shm://artifacts/wf/s1/energy_report/v1",
            "schema_id": "energy_report/1.0",
        }
    }
    request = build_request("wf-data02", step, from_deps)
    assert set(request["data"]) == {"task_model", "energy_report"}
