import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"

FAIL_MARKER = "[stub:fail]"
CLARIFY_MARKER = "[stub:clarify]"


@dataclass
class _Job:
    request: dict[str, Any]
    job_id: str
    polls: int = 0
    final: dict[str, Any] = field(default_factory=dict)


class StubKIO10:
    """In-memory stand-in for KIO10 that speaks the KIO1 <-> KIO10 contract.

    The stub acknowledges every submission, reports ``accepted`` for a
    configurable number of polls, then settles on a final reply. The outcome is
    ``success`` unless the task text carries ``[stub:fail]`` or
    ``[stub:clarify]``. Resubmitting the same ``(workflow_id, step_id)`` returns
    the existing job instead of starting a new one.
    """

    def __init__(self, polls_before_done: int = 1) -> None:
        self._polls_before_done = polls_before_done
        self._jobs: dict[str, _Job] = {}
        self._jobs_by_step: dict[tuple[str, str], str] = {}

    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        """Accept a request and return the documented acknowledgement."""
        key = (request["workflow_id"], request["step_id"])
        job_id = self._jobs_by_step.get(key)
        if job_id is None:
            job_id = f"stub-kio10-job-{len(self._jobs) + 1:04d}"
            self._jobs[job_id] = _Job(request=request, job_id=job_id)
            self._jobs_by_step[key] = job_id
            logger.debug("Stub accepted job: job_id=%s step_id=%s", job_id, key[1])
        else:
            logger.debug("Stub reused job on resubmission: job_id=%s", job_id)

        return _envelope(request, job_id, "accepted")

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """Return the acknowledgement while running, then the final reply."""
        job = self._jobs[job_id]
        if job.polls < self._polls_before_done:
            job.polls += 1
            return _envelope(job.request, job_id, "accepted")

        if not job.final:
            job.final = _final_reply(job.request, job_id)
        return job.final


def _envelope(request: dict[str, Any], job_id: str, status: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": request["workflow_id"],
        "step_id": request["step_id"],
        "job_id": job_id,
        "status": status,
    }


def _final_reply(request: dict[str, Any], job_id: str) -> dict[str, Any]:
    task = request.get("task", "")
    workflow_id = request["workflow_id"]
    step_id = request["step_id"]

    if FAIL_MARKER in task:
        reply = _envelope(request, job_id, "failure")
        reply["failure_class"] = "stub_forced_failure"
        reply["diagnostics"] = {
            "schema_id": "build_log/1.0",
            "receipt": _receipt(workflow_id, step_id, "build_log", job_id),
        }
        return reply

    if CLARIFY_MARKER in task:
        reply = _envelope(request, job_id, "needs_clarification")
        reply["clarification"] = {
            "reason": "stub forced clarification request",
            "options": [
                {"id": "opt-1", "description": "relax accuracy -> fits target as is"},
                {"id": "opt-2", "description": "keep accuracy -> larger target needed"},
            ],
        }
        return reply

    artifact_name = f"{request['capability']}_result"
    reply = _envelope(request, job_id, "success")
    reply["artifacts"] = {
        artifact_name: {
            "schema_id": f"{artifact_name}/1.0",
            "receipt": _receipt(workflow_id, step_id, artifact_name, job_id),
        }
    }
    return reply


def _receipt(
    workflow_id: str, step_id: str, artifact_name: str, job_id: str
) -> dict[str, Any]:
    digest = hashlib.sha256(f"{job_id}/{artifact_name}".encode()).hexdigest()
    return {
        "uri": f"shm://artifacts/{workflow_id}/{step_id}/{artifact_name}/v1",
        "version": 1,
        "content_hash": f"sha256:{digest}",
    }
