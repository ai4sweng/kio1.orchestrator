import asyncio
import json
import logging
from typing import Any, Protocol

import httpx2

from kio10.stub import StubKIO10
from workflow_plan import Step

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
FINAL_STATUSES = frozenset({"success", "needs_clarification", "failure"})
ACCEPTED = "accepted"


class TransportError(Exception):
    """Raised when a KIO10 agent cannot be reached or violates the contract."""


class KIO10Transport(Protocol):
    """The two operations KIO1 needs from a KIO10 endpoint."""

    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a request and return the acknowledgement."""
        ...

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """Return the current state of a job: the acknowledgement or a final reply."""
        ...


def build_request(workflow_id: str, step: Step, data: dict[str, Any]) -> dict[str, Any]:
    """Build a KIO1 -> KIO10 request message in the documented format.

    Args:
        workflow_id: The workflow the step belongs to.
        step: The plan step being dispatched.
        data: References to inputs, keyed by name, each with ``uri`` and ``schema_id``.

    Returns:
        The request as a JSON-serialisable dict.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "step_id": step.step_id,
        "capability": step.capability,
        "task": step.task,
        "data": data,
    }


async def run_job(
    transport: KIO10Transport, request: dict[str, Any], poll_interval: float
) -> dict[str, Any]:
    """Submit a request and poll its job until a final reply arrives.

    Args:
        transport: The KIO10 endpoint to talk to.
        request: The request message built by `build_request`.
        poll_interval: Seconds to wait between polls.

    Returns:
        The final reply, whose ``status`` is one of `FINAL_STATUSES`.

    Raises:
        TransportError: If the acknowledgement or a reply violates the contract.
    """
    ack = await transport.submit(request)
    _check_envelope(ack, request)
    if ack.get("status") != ACCEPTED:
        raise TransportError(
            f"Expected acknowledgement status {ACCEPTED!r}, got {ack.get('status')!r}"
        )
    job_id = ack.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise TransportError("Acknowledgement is missing job_id")
    logger.info("Job accepted: step_id=%s job_id=%s", request["step_id"], job_id)

    while True:
        reply = await transport.get_job(job_id)
        _check_envelope(reply, request)
        status = reply.get("status")
        if status in FINAL_STATUSES:
            logger.info("Job finished: job_id=%s status=%s", job_id, status)
            return reply
        if status != ACCEPTED:
            raise TransportError(f"Unknown job status {status!r} for job {job_id!r}")
        await asyncio.sleep(poll_interval)


def _check_envelope(message: dict[str, Any], request: dict[str, Any]) -> None:
    for key in ("workflow_id", "step_id"):
        if message.get(key) != request[key]:
            raise TransportError(
                f"Reply {key} {message.get(key)!r} does not match request {request[key]!r}"
            )


class HttpKIO10:
    """KIO10 transport over HTTP: ``POST /jobs`` and ``GET /jobs/{job_id}``."""

    def __init__(
        self,
        base_url: str,
        timeout: float,
        httpx_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._httpx_transport = httpx_transport

    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        """POST the request to ``/jobs`` and return the acknowledgement."""
        return await self._call("POST", "/jobs", request)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """GET ``/jobs/{job_id}`` and return the current reply."""
        return await self._call("GET", f"/jobs/{job_id}")

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            async with httpx2.AsyncClient(
                timeout=self._timeout, transport=self._httpx_transport
            ) as client:
                response = await client.request(method, url, json=body)
        except httpx2.HTTPError as error:
            raise TransportError(f"{method} {url} failed: {error}") from error

        if response.status_code >= 400:
            raise TransportError(f"{method} {url} returned HTTP {response.status_code}")
        try:
            parsed = response.json()
        except json.JSONDecodeError as error:
            raise TransportError(f"{method} {url} returned non-JSON body") from error
        if not isinstance(parsed, dict):
            raise TransportError(f"{method} {url} returned non-object JSON")
        return parsed


def create_transport(address: str, timeout: float) -> KIO10Transport:
    """Create a transport for an agent address.

    ``http://`` and ``https://`` addresses talk to a real endpoint;
    ``stub://`` selects the in-memory `StubKIO10`.

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
