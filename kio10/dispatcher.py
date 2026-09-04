import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config_loader import DispatchSettings
from kio10.transport import (
    KIO10Transport,
    TransportError,
    build_request,
    create_transport,
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

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the result."""
        return {
            "step_id": self.step_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "job_id": self.job_id,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
            "reply": self.reply,
        }


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
            "steps": [result.to_dict() for result in self.results],
        }


async def run_workflow(
    plan: WorkflowPlan,
    settings: DispatchSettings,
    transports: Mapping[str, KIO10Transport] | None = None,
) -> DispatchReport:
    """Dispatch every step of a plan to its agent, honouring step dependencies.

    Each step runs as its own task. A step first awaits the tasks of the steps
    it depends on, then submits its request and polls until a final reply.
    Independent steps therefore run concurrently and dependent steps wait for
    exactly their predecessors, whatever ``execution_mode`` says.

    Args:
        plan: The validated workflow plan.
        settings: Dispatch settings, including the agent address registry.
        transports: Optional pre-built transports keyed by agent id. When
            omitted, transports are created from ``settings.agents``.

    Returns:
        A report with one result per plan step, in plan order.
    """
    if transports is None:
        transports = {
            agent_id: create_transport(address, timeout=settings.step_timeout_seconds)
            for agent_id, address in settings.agents.items()
        }

    tasks: dict[str, asyncio.Task[StepResult]] = {}

    async def run_step(step: Step) -> StepResult:
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
    start = time.perf_counter()
    try:
        reply = await asyncio.wait_for(
            run_job(transport, request, settings.poll_interval_seconds),
            timeout=settings.step_timeout_seconds,
        )
    except asyncio.TimeoutError:
        detail = f"timed out after {settings.step_timeout_seconds:g}s"
        logger.error("Step timed out: step_id=%s", step.step_id)
        return StepResult(
            step.step_id,
            step.agent_id,
            ERROR,
            duration_ms=_elapsed(start),
            detail=detail,
        )
    except TransportError as error:
        logger.error("Step transport error: step_id=%s error=%s", step.step_id, error)
        return StepResult(
            step.step_id,
            step.agent_id,
            ERROR,
            duration_ms=_elapsed(start),
            detail=str(error),
        )

    return StepResult(
        step.step_id,
        step.agent_id,
        reply["status"],
        job_id=reply.get("job_id"),
        duration_ms=_elapsed(start),
        detail=_describe(reply),
        reply=reply,
    )


def _elapsed(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _describe(reply: dict[str, Any]) -> str:
    status = reply["status"]
    if status == SUCCESS:
        names = sorted(reply.get("artifacts") or {})
        return f"artifacts: {', '.join(names)}" if names else "no artifacts"
    if status == FAILURE:
        return f"failure_class={reply.get('failure_class')}"
    clarification = reply.get("clarification") or {}
    return f"clarification: {clarification.get('reason', '')}"


def _collect_artifacts(dependencies: list[StepResult]) -> dict[str, Any]:
    """Turn the artifacts of finished dependencies into ``data`` references.

    Each artifact becomes ``{name: {uri, schema_id}}``. When two dependencies
    produce the same artifact name, later ones are prefixed with their step id.
    """
    data: dict[str, Any] = {}
    for dependency in dependencies:
        artifacts = (dependency.reply or {}).get("artifacts") or {}
        for name, artifact in artifacts.items():
            receipt = artifact.get("receipt") or {}
            uri = receipt.get("uri")
            if not uri:
                logger.warning(
                    "Artifact without receipt uri ignored: step_id=%s artifact=%s",
                    dependency.step_id,
                    name,
                )
                continue
            key = name if name not in data else f"{dependency.step_id}.{name}"
            data[key] = {"uri": uri, "schema_id": artifact.get("schema_id")}
    return data


def format_report(report: DispatchReport) -> str:
    """Render a report as a compact table for the terminal."""
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
