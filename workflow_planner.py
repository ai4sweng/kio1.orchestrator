"""Validate KIO plans and explicitly dispatch tasks to trusted HTTP services."""

import hashlib
import http.client
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from prompt_loader import load_capabilities
from telemetry import trace_operation

logger = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 1024 * 1024
TASK_TIMEOUT_SECONDS = 60
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


class WorkflowConfigurationError(ValueError):
    """Indicate disabled dispatch or an invalid trusted agent registry."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent a configured endpoint from redirecting tasks or credentials."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        """Reject every HTTP redirect.

        Args:
            req: Original HTTP request.
            fp: Response stream supplied by urllib.
            code: Redirect HTTP status.
            msg: HTTP status message.
            headers: Response headers.
            newurl: Redirect destination, which is never followed.

        Returns:
            None, instructing urllib to reject the redirect.
        """
        return None


def load_agent_registry(path: Path) -> dict[str, dict[str, str]]:
    """Load trusted task endpoints and optional bearer-token environment names.

    Args:
        path: JSON registry mapping catalog agent IDs to connection settings.

    Returns:
        Agent IDs mapped to validated endpoint and optional token_env settings.

    Raises:
        WorkflowConfigurationError: The registry, catalog, or credentials are invalid.
    """
    try:
        catalog = load_capabilities()
        registry = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(registry, dict) or not registry:
            raise ValueError("Registry must be a nonempty object.")
        for agent_id, settings in registry.items():
            if agent_id not in catalog or not isinstance(settings, dict):
                raise ValueError("Unknown agent or invalid settings.")
            endpoint = settings.get("endpoint")
            if not isinstance(endpoint, str):
                raise ValueError("Each agent needs an endpoint.")
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Invalid HTTP task endpoint.")
            token_env = settings.get("token_env")
            if token_env is not None and (
                not isinstance(token_env, str) or not os.environ.get(token_env)
            ):
                raise ValueError(
                    "Configured bearer-token environment variable is empty."
                )
    except (OSError, ValueError) as error:
        raise WorkflowConfigurationError(
            "Agent registry is missing or invalid."
        ) from error
    return registry


def validate_plan(plan: Any) -> list[dict[str, Any]]:
    """Validate every step and dependency before allowing any network traffic.

    Args:
        plan: Untrusted JSON workflow with an ID, execution mode, and steps.

    Returns:
        Normalized steps containing validated catalog capabilities, data, and
        dependency IDs, including implicit sequential dependencies.

    Raises:
        ValueError: The plan, dependencies, or capability catalog are invalid.
    """
    catalog = load_capabilities()
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object.")
    try:
        encoded = json.dumps(plan, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError(
            "plan must contain finite, JSON-serializable values."
        ) from error
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ValueError("plan must not exceed 1 MiB.")
    workflow_id = plan.get("workflow_id")
    if not isinstance(workflow_id, str) or not IDENTIFIER.fullmatch(workflow_id):
        raise ValueError(
            "workflow_id must be a safe identifier of at most 128 characters."
        )
    mode = plan.get("execution_mode")
    if mode not in ("sequential", "parallel", "mixed"):
        raise ValueError("execution_mode must be sequential, parallel, or mixed.")
    raw_steps = plan.get("steps")
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 64:
        raise ValueError("steps must contain between 1 and 64 tasks.")
    steps: list[dict[str, Any]] = []
    step_ids: set[str] = set()
    previous_id = None
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise ValueError("Each step must be an object.")
        step_id = raw_step.get("step_id")
        agent_id = raw_step.get("agent_id")
        capability = raw_step.get("capability")
        task = raw_step.get("task")
        if (
            not isinstance(step_id, str)
            or not IDENTIFIER.fullmatch(step_id)
            or step_id in step_ids
        ):
            raise ValueError("step_id values must be unique, safe identifiers.")
        if (
            not isinstance(agent_id, str)
            or agent_id not in catalog
            or not isinstance(capability, str)
            or capability not in catalog[agent_id]["capabilities"]
        ):
            raise ValueError("Agent and capability must match the KIO2-KIO13 catalog.")
        if not isinstance(task, str) or not task.strip() or len(task) > 16000:
            raise ValueError("Each task must contain 1-16000 nonblank characters.")
        if "endpoint" in raw_step:
            raise ValueError("Task endpoints must come from the trusted registry.")
        dependencies = raw_step.get("depends_on", [])
        if mode == "mixed" and "depends_on" not in raw_step:
            raise ValueError("Every mixed-plan step must specify depends_on.")
        if not isinstance(dependencies, list) or any(
            not isinstance(dependency, str) for dependency in dependencies
        ):
            raise ValueError("depends_on must be a list of step IDs.")
        if mode == "parallel" and dependencies:
            raise ValueError("Parallel-plan steps must be independent.")
        if mode == "sequential" and previous_id and previous_id not in dependencies:
            dependencies = [*dependencies, previous_id]
        data = raw_step.get("data", {})
        if not isinstance(data, dict):
            raise ValueError("Step data must be an object.")
        steps.append(
            {
                "step_id": step_id,
                "agent_id": agent_id,
                "capability": capability,
                "task": task,
                "data": data,
                "depends_on": dependencies,
            }
        )
        step_ids.add(step_id)
        previous_id = step_id
    resolved: set[str] = set()
    while len(resolved) < len(steps):
        ready = {
            step["step_id"]
            for step in steps
            if step["step_id"] not in resolved and set(step["depends_on"]) <= resolved
        }
        if not ready:
            raise ValueError(
                "Dependencies contain a cycle, self-reference, or unknown ID."
            )
        resolved.update(ready)
    return steps


def _send_task(
    endpoint: str, token_env: str | None, payload: dict[str, Any]
) -> dict[str, Any]:
    """Send once, bounding response size and rejecting uncorrelated results.

    Prints the outgoing JSON body to stdout before attempting delivery.
    Authentication headers are not printed.

    Args:
        endpoint: Trusted full HTTP task URL from the agent registry.
        token_env: Optional environment variable holding the bearer token.
        payload: Versioned JSON task with correlation fields and idempotency key.

    Returns:
        A correlated terminal agent response, a local failed result for oversized
        input, or an unknown outcome when transport or response validation fails.
    """
    encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        logger.warning(
            "Task rejected: workflow_id=%s step_id=%s status=failed "
            "error_code=INPUT_TOO_LARGE request_bytes=%d",
            payload["workflow_id"],
            payload["step_id"],
            len(encoded),
        )
        return {
            "step_id": payload["step_id"],
            "status": "failed",
            "error": {
                "code": "INPUT_TOO_LARGE",
                "message": "Task including dependency results exceeds 1 MiB; use artifact references.",
                "retryable": False,
            },
        }
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": payload["idempotency_key"],
    }
    if token_env:
        headers["Authorization"] = f"Bearer {os.environ[token_env]}"
    request = urllib.request.Request(
        endpoint, data=encoded, headers=headers, method="POST"
    )
    print(
        f"\n--- KIO1 agent payload JSON (workflow {payload['workflow_id']}, "
        f"step {payload['step_id']}, agent {payload['agent_id']}) ---\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)}\n"
        "--- end agent payload JSON ---\n",
        flush=True,
    )
    started = time.monotonic()
    status = "unknown"
    phase = "connect"
    http_status: int | None = None
    response_bytes = 0
    logger.info(
        "Task dispatch: workflow_id=%s step_id=%s agent_id=%s idempotency_key=%s",
        payload["workflow_id"],
        payload["step_id"],
        payload["agent_id"],
        payload["idempotency_key"],
    )
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        with trace_operation(
            "kio1.workflow.task",
            {
                "workflow.id": payload["workflow_id"],
                "workflow.step.id": payload["step_id"],
                "workflow.agent.id": payload["agent_id"],
            },
        ), opener.open(request, timeout=TASK_TIMEOUT_SECONDS) as response:
            http_status = response.status
            phase = "http_status"
            if response.status != 200:
                raise ValueError("Expected a synchronous HTTP 200 result.")
            phase = "read_response"
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            response_bytes = len(raw)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("Task response exceeds 1 MiB.")
            phase = "parse_response"
            result = json.loads(raw)
            json.dumps(result, allow_nan=False)
        phase = "validate_response"
        if not isinstance(result, dict):
            raise ValueError("Task result must be an object.")
        for field in (
            "schema_version",
            "workflow_id",
            "step_id",
            "agent_id",
            "idempotency_key",
        ):
            if result.get(field) != payload[field]:
                raise ValueError("Task response correlation mismatch.")
        if result.get("status") not in ("succeeded", "failed"):
            raise ValueError("Task response must have a terminal status.")
        if result["status"] == "succeeded" and not isinstance(
            result.get("result"), dict
        ):
            raise ValueError("Successful task result must be an object.")
        error = result.get("error")
        if result["status"] == "failed" and (
            not isinstance(error, dict)
            or not isinstance(error.get("code"), str)
            or not isinstance(error.get("message"), str)
            or not isinstance(error.get("retryable"), bool)
        ):
            raise ValueError("Failed tasks must include a structured error.")
        status = result["status"]
        phase = "complete"
        return result
    except (OSError, ValueError, http.client.HTTPException, RecursionError) as error:
        if isinstance(error, urllib.error.HTTPError):
            http_status = error.code
            phase = "http_status"
        logger.warning(
            "Task outcome unknown: workflow_id=%s step_id=%s agent_id=%s "
            "phase=%s http_status=%s error_type=%s",
            payload["workflow_id"],
            payload["step_id"],
            payload["agent_id"],
            phase,
            http_status,
            type(error).__name__,
        )
        return {
            "step_id": payload["step_id"],
            "agent_id": payload["agent_id"],
            "status": "unknown",
            "error": {
                "code": "DISPATCH_UNCERTAIN",
                "message": "No valid terminal response; inspect the agent before resubmitting.",
                "retryable": False,
            },
        }
    finally:
        logger.info(
            "Task dispatch finished: workflow_id=%s step_id=%s agent_id=%s "
            "status=%s duration_ms=%d http_status=%s request_bytes=%d response_bytes=%d",
            payload["workflow_id"],
            payload["step_id"],
            payload["agent_id"],
            status,
            int((time.monotonic() - started) * 1000),
            http_status,
            len(encoded),
            response_bytes,
        )


def dispatch_workflow(plan: Any, registry: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Run ready tasks in bounded batches; stop scheduling after any failure.

    Args:
        plan: Untrusted workflow plan to validate before any dispatch.
        registry: Validated agent endpoints and optional token_env settings.

    Returns:
        Workflow ID, aggregate status, and step outcomes including blocked steps.

    Raises:
        ValueError: The plan or capability catalog is invalid.
        WorkflowConfigurationError: A selected agent has no configured endpoint.
    """
    started = time.monotonic()
    try:
        steps = validate_plan(plan)
        if any(step["agent_id"] not in registry for step in steps):
            raise WorkflowConfigurationError(
                "Every selected agent needs a configured endpoint."
            )
    except ValueError as error:
        logger.warning(
            "Workflow rejected: duration_ms=%d error_type=%s",
            int((time.monotonic() - started) * 1000),
            type(error).__name__,
        )
        raise
    logger.info(
        "Workflow started: workflow_id=%s execution_mode=%s step_count=%d validation_duration_ms=%d",
        plan["workflow_id"],
        plan["execution_mode"],
        len(steps),
        int((time.monotonic() - started) * 1000),
    )
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        while len(results) < len(steps):
            ready = [
                step
                for step in steps
                if step["step_id"] not in results
                and all(dependency in results for dependency in step["depends_on"])
            ][:4]
            logger.info(
                "Workflow batch ready: workflow_id=%s step_ids=%s",
                plan["workflow_id"],
                ",".join(step["step_id"] for step in ready),
            )
            pending = []
            for step in ready:
                inputs = {
                    "workflow_id": plan["workflow_id"],
                    "step_id": step["step_id"],
                    "agent_id": step["agent_id"],
                    "capability": step["capability"],
                    "task": step["task"],
                    "data": step["data"],
                    "dependency_results": {
                        dependency: results[dependency]["result"]
                        for dependency in step["depends_on"]
                    },
                }
                key = hashlib.sha256(
                    json.dumps(inputs, sort_keys=True, allow_nan=False).encode("utf-8")
                ).hexdigest()
                payload = {
                    "schema_version": "1.0",
                    **inputs,
                    "idempotency_key": key,
                    "deadline_at": (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=TASK_TIMEOUT_SECONDS)
                    ).isoformat(),
                }
                settings = registry[step["agent_id"]]
                pending.append(
                    (
                        step["step_id"],
                        executor.submit(
                            _send_task,
                            settings["endpoint"],
                            settings.get("token_env"),
                            payload,
                        ),
                    )
                )
            for step_id, future in pending:
                results[step_id] = future.result()
                step_error = results[step_id].get("error")
                logger.info(
                    "Task result: workflow_id=%s step_id=%s status=%s error_code=%r",
                    plan["workflow_id"],
                    step_id,
                    results[step_id]["status"],
                    step_error.get("code") if isinstance(step_error, dict) else None,
                )
            if any(result["status"] != "succeeded" for result in results.values()):
                break
    for step in steps:
        if step["step_id"] not in results:
            results[step["step_id"]] = {"step_id": step["step_id"], "status": "blocked"}
            logger.warning(
                "Task blocked: workflow_id=%s step_id=%s agent_id=%s reason=workflow_stopped",
                plan["workflow_id"],
                step["step_id"],
                step["agent_id"],
            )
    status = "succeeded"
    if any(result["status"] == "unknown" for result in results.values()):
        status = "unknown"
    elif any(result["status"] != "succeeded" for result in results.values()):
        status = "failed"
    counts = {
        outcome: sum(result["status"] == outcome for result in results.values())
        for outcome in ("succeeded", "failed", "unknown", "blocked")
    }
    logger.info(
        "Workflow completed: workflow_id=%s status=%s duration_ms=%d "
        "step_count=%d succeeded=%d failed=%d unknown=%d blocked=%d",
        plan["workflow_id"],
        status,
        int((time.monotonic() - started) * 1000),
        len(steps),
        counts["succeeded"],
        counts["failed"],
        counts["unknown"],
        counts["blocked"],
    )
    return {
        "workflow_id": plan["workflow_id"],
        "status": status,
        "steps": list(results.values()),
    }
