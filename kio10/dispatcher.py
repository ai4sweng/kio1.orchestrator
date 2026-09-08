"""Dispatch plan steps to KIO agents in dependency order and report the results."""

import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from config_loader import DispatchSettings
from kio10.stub import StubKIO10
from kio10.transport import (
    HttpKIO10,
    KIO10Transport,
    TransportError,
    build_request,
    run_job,
)
from workflow_plan import Step, WorkflowPlan, parse_plan

logger = logging.getLogger(__name__)

SUCCESS = "success"
FAILURE = "failure"
NEEDS_CLARIFICATION = "needs_clarification"
SKIPPED = "skipped"
ERROR = "error"


@dataclass
class StepResult:
    """Outcome of dispatching one plan step."""

    step_id: str
    agent_id: str
    status: str
    job_id: str | None = None
    duration_ms: int = 0
    detail: str = ""
    reply: dict[str, Any] | None = field(default=None, repr=False)


@dataclass
class DispatchReport:
    """Results of dispatching a whole workflow, in plan order."""

    workflow_id: str
    results: list[StepResult]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the report."""
        return {
            "workflow_id": self.workflow_id,
            "summary": dict(Counter(result.status for result in self.results)),
            "steps": [asdict(result) for result in self.results],
        }


def create_transport(address: str, timeout: float) -> KIO10Transport:
    """Create a transport for an agent address.

    `http://` and `https://` addresses talk to a real endpoint; `stub://`
    selects the in-memory `StubKIO10`.

    Args:
        address: The agent address from configuration.
        timeout: Per-request timeout in seconds for HTTP transports.

    Returns:
        A transport implementing `KIO10Transport`.

    Raises:
        ValueError: If the address scheme is not supported.
    """
    if address.startswith("stub://"):
        return StubKIO10()
    if address.startswith(("http://", "https://")):
        return HttpKIO10(address, timeout=timeout)
    raise ValueError(f"Unsupported agent address: {address!r}")


async def run_workflow(
    plan: WorkflowPlan,
    settings: DispatchSettings,
    transports: Mapping[str, KIO10Transport] | None = None,
) -> DispatchReport:
    """Dispatch every step of a plan to its agent, honouring step dependencies.

    Each step runs as its own task. A step first awaits the tasks of the steps
    it depends on, then submits its request and polls until a final reply.
    Independent steps therefore run concurrently and dependent steps wait for
    exactly their predecessors, whatever `execution_mode` says. At most
    `settings.max_parallel_steps` steps are in flight at once.

    Args:
        plan: The validated workflow plan.
        settings: Dispatch settings, including the agent address registry.
        transports: Optional pre-built transports keyed by agent id, owned and
            closed by the caller. When omitted, transports are created from
            `settings.agents` and closed when the workflow finishes.

    Returns:
        A report with one result per plan step, in plan order.
    """
    owned: list[KIO10Transport] = []
    if transports is None:
        transports = {
            agent_id: create_transport(address, timeout=settings.step_timeout_seconds)
            for agent_id, address in settings.agents.items()
        }
        owned = list(transports.values())

    try:
        return await _run_steps(plan, settings, transports)
    finally:
        await _close_all(owned)


async def _close_all(transports: list[KIO10Transport]) -> None:
    """Close every transport, logging failures instead of skipping the rest.

    Args:
        transports: The transports created by `run_workflow`.
    """
    outcomes = await asyncio.gather(
        *(transport.aclose() for transport in transports), return_exceptions=True
    )
    for transport, outcome in zip(transports, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.warning(
                "Transport close failed: transport=%s error=%s", transport, outcome
            )


async def _run_steps(
    plan: WorkflowPlan,
    settings: DispatchSettings,
    transports: Mapping[str, KIO10Transport],
) -> DispatchReport:
    """Run the plan's steps against ready transports and build the report.

    Args:
        plan: The validated workflow plan.
        settings: Dispatch settings.
        transports: Transports keyed by agent id; missing agents are skipped.

    Returns:
        A report with one result per plan step, in plan order.
    """
    tasks: dict[str, asyncio.Task[StepResult]] = {}
    # Acquired only around the agent call, never while waiting for dependencies,
    # so a limit of 1 still lets a chain of dependent steps complete.
    slots = asyncio.Semaphore(settings.max_parallel_steps)

    async def run_step(step: Step) -> StepResult:
        """Run one step after its dependencies and return its result."""
        dependencies = [await tasks[dep_id] for dep_id in step.depends_on]

        blocker = next((dep for dep in dependencies if dep.status != SUCCESS), None)
        if blocker is not None:
            return StepResult(
                step.step_id,
                step.agent_id,
                SKIPPED,
                detail=f"dependency {blocker.step_id} ended with {blocker.status}",
            )

        transport = transports.get(step.agent_id)
        if transport is None:
            return StepResult(
                step.step_id,
                step.agent_id,
                SKIPPED,
                detail=f"agent {step.agent_id} not deployed",
            )

        request = build_request(
            plan.workflow_id, step, _collect_artifacts(dependencies)
        )
        async with slots:
            return await _execute(step, transport, request, settings)

    for step in plan.steps:
        tasks[step.step_id] = asyncio.create_task(run_step(step))

    results = await asyncio.gather(*tasks.values())
    report = DispatchReport(plan.workflow_id, list(results))
    logger.info(
        "Workflow dispatched: workflow_id=%s summary=%s",
        plan.workflow_id,
        report.to_dict()["summary"],
    )
    return report


async def _execute(
    step: Step,
    transport: KIO10Transport,
    request: dict[str, Any],
    settings: DispatchSettings,
) -> StepResult:
    """Submit one step to its agent and wait for the final reply.

    Args:
        step: The plan step being dispatched.
        transport: The agent's transport.
        request: The request message built for the step.
        settings: Dispatch settings providing the poll interval and step timeout.

    Returns:
        A result carrying the agent's final status, or `error` when the step
        timed out or the transport failed.
    """
    start = time.perf_counter()
    reply: dict[str, Any] | None = None
    try:
        reply = await asyncio.wait_for(
            run_job(transport, request, settings.poll_interval_seconds),
            timeout=settings.step_timeout_seconds,
        )
    except asyncio.TimeoutError:
        status = ERROR
        detail = f"timed out after {settings.step_timeout_seconds:g}s"
        logger.error("Step timed out: step_id=%s", step.step_id)
    except TransportError as error:
        status = ERROR
        detail = str(error)
        logger.error("Step transport error: step_id=%s error=%s", step.step_id, error)
    else:
        status = reply["status"]
        detail = _describe(reply)

    return StepResult(
        step.step_id,
        step.agent_id,
        status,
        job_id=reply.get("job_id") if reply else None,
        duration_ms=int((time.perf_counter() - start) * 1000),
        detail=detail,
        reply=reply,
    )


def _describe(reply: dict[str, Any]) -> str:
    """Summarise a final reply in one line for the report.

    Args:
        reply: The agent's final reply.

    Returns:
        The artifact names for `success`, the failure class for `failure`, or the
        reason for `needs_clarification`.
    """
    status = reply["status"]
    if status == SUCCESS:
        names = sorted(reply.get("artifacts") or {})
        return f"artifacts: {', '.join(names)}" if names else "no artifacts"
    if status == FAILURE:
        return f"failure_class={reply.get('failure_class')}"
    clarification = reply.get("clarification") or {}
    return f"clarification: {clarification.get('reason', '')}"


def _collect_artifacts(dependencies: list[StepResult]) -> dict[str, Any]:
    """Turn the artifacts of finished dependencies into `data` references.

    Args:
        dependencies: Results of the steps this step depends on, all successful.

    Returns:
        A mapping `{name: {uri, schema_id}}`, one entry per artifact. When two
        dependencies produce the same artifact name, later ones are prefixed
        with their step id. The artifact shape was already checked by `run_job`.
    """
    data: dict[str, Any] = {}
    for dependency in dependencies:
        artifacts = (dependency.reply or {}).get("artifacts") or {}
        for name, artifact in artifacts.items():
            key = name if name not in data else f"{dependency.step_id}.{name}"
            data[key] = {
                "uri": artifact["receipt"]["uri"],
                "schema_id": artifact.get("schema_id"),
            }
    return data


def format_report(report: DispatchReport) -> str:
    """Render a report as a compact table for the terminal.

    Args:
        report: The report to render.

    Returns:
        One header line, one line per step, and a summary line.
    """
    lines = [f"Dispatch report: {report.workflow_id}"]
    for result in report.results:
        lines.append(
            f"  {result.step_id:<5} {result.agent_id:<7} {result.status:<20} "
            f"{result.duration_ms:>7} ms  {result.detail}"
        )
    summary = report.to_dict()["summary"]
    lines.append(
        "Summary: "
        + ", ".join(f"{status}={count}" for status, count in summary.items())
    )
    return "\n".join(lines)


def write_report(report: DispatchReport, log_directory: str, session_id: str) -> Path:
    """Store the report as JSON next to the session log.

    Args:
        report: The report to store.
        log_directory: Directory holding session logs.
        session_id: The session id shared with the log and chat files.

    Returns:
        The path of the written file.
    """
    directory = Path(log_directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"dispatch_{session_id}_{report.workflow_id}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    logger.info("Dispatch report written: path=%s", path)
    return path


def dispatch_plan(
    plan_json: str, settings: DispatchSettings, log_directory: str, session_id: str
) -> str:
    """Parse a plan, dispatch it, store the report and return a terminal summary.

    Args:
        plan_json: The plan as produced by the planner model.
        settings: Dispatch settings.
        log_directory: Directory holding session logs.
        session_id: The current session id.

    Returns:
        The formatted report followed by the report file path.

    Raises:
        ValueError: If the plan is not valid.
    """
    plan = parse_plan(json.loads(plan_json))
    report = asyncio.run(run_workflow(plan, settings))
    path = write_report(report, log_directory, session_id)
    return f"{format_report(report)}\nReport saved to {path}"
