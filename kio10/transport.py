"""KIO1 <-> KIO10 message contract and the transports that carry it."""

import asyncio
import json
import logging
import re
from typing import Any, Protocol
from urllib.parse import quote

import httpx2

from workflow_plan import Step

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
FINAL_STATUSES = frozenset({"success", "needs_clarification", "failure"})
ACCEPTED = "accepted"

# A job id is placed into a URL path, so only plain identifier characters are
# accepted: no path separators, query characters or whitespace.
_JOB_ID = re.compile(r"^[A-Za-z0-9._:-]+$")


class TransportError(Exception):
    """Raised when a KIO10 agent cannot be reached or violates the contract."""


class KIO10Transport(Protocol):
    """The operations KIO1 needs from a KIO10 endpoint."""

    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a request and return the acknowledgement.

        Args:
            request: The KIO1 -> KIO10 request message.

        Returns:
            The acknowledgement with `job_id` and `status: "accepted"`.
        """
        ...

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """Return the current state of a job.

        Args:
            job_id: The job id from the acknowledgement.

        Returns:
            The acknowledgement while the job is running, otherwise the final
            reply.
        """
        ...

    async def aclose(self) -> None:
        """Release any connection the transport holds."""
        ...


def build_request(workflow_id: str, step: Step, data: dict[str, Any]) -> dict[str, Any]:
    """Build a KIO1 -> KIO10 request message in the documented format.

    The request's `data` starts with the references the plan declared on the
    step and adds the artifacts of finished dependencies; a dependency
    artifact overrides a same-named plan reference, because it is the fresher
    result.

    Args:
        workflow_id: The workflow the step belongs to.
        step: The plan step being dispatched.
        data: References from dependency artifacts, keyed by name, each with
            `uri` and `schema_id`.

    Returns:
        The request as a JSON-serialisable dict.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "step_id": step.step_id,
        "capability": step.capability,
        "task": step.task,
        "data": {**step.data, **data},
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
        The final reply, whose `status` is one of `FINAL_STATUSES`.

    Raises:
        TransportError: If the acknowledgement or a reply violates the contract,
            including a poll reply that belongs to a different job.
    """
    ack = await transport.submit(request)
    _check_envelope(ack, request)
    if ack.get("status") != ACCEPTED:
        raise TransportError(
            f"Expected acknowledgement status {ACCEPTED!r}, got {ack.get('status')!r}"
        )
    job_id = ack.get("job_id")
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise TransportError(
            f"Acknowledgement job_id {job_id!r} is missing or not a plain identifier"
        )
    logger.info("Job accepted: step_id=%s job_id=%s", request["step_id"], job_id)

    while True:
        reply = await transport.get_job(job_id)
        _check_envelope(reply, request)
        if reply.get("job_id") != job_id:
            raise TransportError(
                f"Reply job_id {reply.get('job_id')!r} does not match "
                f"acknowledged job {job_id!r}"
            )
        status = reply.get("status")
        if status in FINAL_STATUSES:
            _check_final_body(reply)
            logger.info("Job finished: job_id=%s status=%s", job_id, status)
            return reply
        if status != ACCEPTED:
            raise TransportError(f"Unknown job status {status!r} for job {job_id!r}")
        await asyncio.sleep(poll_interval)


def _check_envelope(message: dict[str, Any], request: dict[str, Any]) -> None:
    """Ensure a reply belongs to the request it answers and speaks our schema.

    Args:
        message: An acknowledgement or a reply from the agent.
        request: The request that was sent.

    Raises:
        TransportError: If `schema_version` is not the version KIO1 speaks, or
            `workflow_id` or `step_id` differ from the request.
    """
    schema_version = message.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise TransportError(
            f"Reply schema_version {schema_version!r} is not supported; "
            f"expected {SCHEMA_VERSION!r}"
        )
    for key in ("workflow_id", "step_id"):
        if message.get(key) != request[key]:
            raise TransportError(
                f"Reply {key} {message.get(key)!r} does not match request {request[key]!r}"
            )


def _check_final_body(reply: dict[str, Any]) -> None:
    """Ensure the parts of a final reply that KIO1 reads have the documented shape.

    Only what the dispatcher consumes is checked: `artifacts` (a mapping of
    name to an object with `receipt.uri`), `clarification` (an object) and
    `failure_class` (a string). Anything else in the body is passed through.

    Args:
        reply: A reply whose `status` is one of `FINAL_STATUSES`.

    Raises:
        TransportError: If one of those parts is present but malformed.
    """
    artifacts = reply.get("artifacts")
    if artifacts is not None:
        if not isinstance(artifacts, dict):
            raise TransportError("Reply is malformed: artifacts must be an object")
        for name, artifact in artifacts.items():
            receipt = artifact.get("receipt") if isinstance(artifact, dict) else None
            if not isinstance(receipt, dict) or not isinstance(receipt.get("uri"), str):
                raise TransportError(
                    f"Reply is malformed: artifact {name!r} needs a receipt with a "
                    "string uri"
                )

    clarification = reply.get("clarification")
    if clarification is not None and not isinstance(clarification, dict):
        raise TransportError("Reply is malformed: clarification must be an object")

    failure_class = reply.get("failure_class")
    if failure_class is not None and not isinstance(failure_class, str):
        raise TransportError("Reply is malformed: failure_class must be a string")


class HttpKIO10:
    """KIO10 transport over HTTP: `POST /jobs` and `GET /jobs/{job_id}`.

    One `httpx2.AsyncClient` is opened on the first request and reused by every
    later request, so polling keeps its connection instead of reconnecting. The
    client belongs to the event loop that opened it, and the transport refuses
    requests from any other loop: create one transport per loop. Call `aclose`
    when the transport is no longer needed; after that it refuses all requests.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float,
        httpx_transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Create an HTTP transport for one agent.

        Args:
            base_url: The agent's base URL, for example `http://localhost:8010`.
            timeout: Per-request timeout in seconds.
            httpx_transport: Optional `httpx2` transport, used by tests to serve
                responses from memory.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._httpx_transport = httpx_transport
        self._client: httpx2.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        """POST the request to `/jobs` and return the acknowledgement.

        Args:
            request: The KIO1 -> KIO10 request message.

        Returns:
            The acknowledgement as returned by the agent.

        Raises:
            TransportError: If the agent cannot be reached or returns an error
                or a non-JSON body.
        """
        return await self._call("POST", "/jobs", request)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """GET `/jobs/{job_id}` and return the current reply.

        Args:
            job_id: The job id from the acknowledgement; encoded as one path
                segment so it cannot alter the request path or query.

        Returns:
            The acknowledgement while the job is running, otherwise the final
        reply.

        Raises:
            TransportError: If the agent cannot be reached or returns an error
                or a non-JSON body.
        """
        return await self._call("GET", f"/jobs/{quote(job_id, safe='')}")

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Perform one HTTP request against the agent.

        Args:
            method: The HTTP method.
            path: The path appended to the base URL.
            body: Optional JSON body.

        Returns:
            The parsed JSON object from the response.

        Raises:
            TransportError: On connection failure, an HTTP status of 400 or above,
                a non-JSON body, or a JSON body that is not an object.
        """
        url = f"{self._base_url}{path}"
        try:
            response = await self._get_client().request(method, url, json=body)
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

    def _get_client(self) -> httpx2.AsyncClient:
        """Return the shared client, opening it on first use.

        Returns:
            The `httpx2.AsyncClient` shared by every request of this transport.

        Raises:
            TransportError: If the transport has been closed, or the client was
                opened under a different event loop.
        """
        if self._closed:
            raise TransportError("Transport is closed")
        loop = asyncio.get_running_loop()
        if self._client is None:
            self._client = httpx2.AsyncClient(
                timeout=self._timeout, transport=self._httpx_transport
            )
            self._loop = loop
        elif self._loop is not loop:
            raise TransportError(
                "Transport is bound to another event loop; create one transport "
                "per loop"
            )
        return self._client

    async def aclose(self) -> None:
        """Close the shared client and refuse further requests. Idempotent."""
        self._closed = True
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
