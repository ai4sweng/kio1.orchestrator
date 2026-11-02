"""Test-only KIO receiver with silent health checks and exact-body output."""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

AGENT_IDS = frozenset(f"KIO{number}" for number in range(2, 14))
MAX_BODY_BYTES = 1024 * 1024
SEPARATOR = b"-" * 72
PRINT_LOCK = threading.Lock()
CORRELATION_FIELDS = (
    "schema_version",
    "workflow_id",
    "step_id",
    "agent_id",
    "idempotency_key",
)


class DummyKIOServer(ThreadingHTTPServer):
    """HTTP server carrying the configured identity of one dummy agent."""

    def __init__(self, address: tuple[str, int], agent_id: str) -> None:
        """Initialize a receiver with a stable per-server agent identity.

        Args:
            address: Interface and port on which to listen.
            agent_id: Supported agent ID from KIO2 through KIO13.

        Returns:
            No value; binds the listening socket and stores the agent identity.

        Raises:
            ValueError: The requested agent ID is unsupported.
        """
        if agent_id not in AGENT_IDS:
            raise ValueError("KIO_AGENT_ID must be KIO2 through KIO13.")
        self.agent_id = agent_id
        super().__init__(address, DummyKIOHandler)


class DummyKIOHandler(BaseHTTPRequestHandler):
    """Print task bodies without startup, health, or HTTP access-log noise."""

    server: DummyKIOServer

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress the standard HTTP request and error access logs.

        Args:
            format: Access-log format supplied by the base HTTP handler.
            args: Format arguments intentionally not emitted.

        Returns:
            No value; writes nothing to stdout or stderr.
        """
        return None

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        """Send one JSON response without printing its contents.

        Args:
            status: HTTP status code to send.
            payload: JSON-serializable response object.

        Returns:
            No value; writes headers and body to the requesting client.
        """
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Serve the health endpoint silently.

        Takes the request path and agent identity from the active connection.

        Returns:
            No value; returns health JSON for /health and HTTP 404 otherwise.
        """
        if self.path != "/health":
            self._send_json(404, {"error": "Not found."})
            return
        self._send_json(
            200, {"status": "ok", "agent_id": self.server.agent_id, "dummy": True}
        )

    def do_POST(self) -> None:
        """Print an incoming task body followed by a separator and acknowledge it.

        Reads Content-Type, Content-Length, Idempotency-Key, and the task body
        from the HTTP request. The original bytes are never reformatted.

        Returns:
            No value; prints a complete bounded body and separator to stdout,
            then sends correlated success JSON or an HTTP validation error.
        """
        if self.path != "/tasks":
            self._send_json(404, {"error": "Not found."})
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json(415, {"error": "Content-Type must be application/json."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid Content-Length."})
            return
        if not 0 < length <= MAX_BODY_BYTES:
            self._send_json(
                413 if length > MAX_BODY_BYTES else 400,
                {"error": "Task body must contain 1 byte to 1 MiB."},
            )
            return
        self.connection.settimeout(10)
        try:
            body = self.rfile.read(length)
        except TimeoutError:
            self._send_json(408, {"error": "Timed out reading the task body."})
            return
        if len(body) != length:
            self._send_json(400, {"error": "Incomplete task body."})
            return
        with PRINT_LOCK:
            sys.stdout.buffer.write(body + b"\n" + SEPARATOR + b"\n")
            sys.stdout.buffer.flush()
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._send_json(400, {"error": "Task body must be valid JSON."})
            return
        if not isinstance(payload, dict) or any(
            not isinstance(payload.get(field), str) or not payload[field]
            for field in CORRELATION_FIELDS
        ):
            self._send_json(400, {"error": "Missing task correlation fields."})
            return
        if (
            payload["schema_version"] != "1.0"
            or payload["agent_id"] != self.server.agent_id
        ):
            self._send_json(400, {"error": "Unsupported schema or incorrect agent ID."})
            return
        if self.headers.get("Idempotency-Key") != payload["idempotency_key"]:
            self._send_json(
                400, {"error": "Idempotency-Key header must match the body."}
            )
            return
        self._send_json(
            200,
            {
                **{field: payload[field] for field in CORRELATION_FIELDS},
                "status": "succeeded",
                "result": {
                    "dummy": True,
                    "received": True,
                    "agent_id": self.server.agent_id,
                    "received_bytes": len(body),
                },
            },
        )


def main() -> None:
    """Run the selected dummy agent without startup or routine HTTP messages.

    Takes KIO_AGENT_ID from the environment, defaulting to KIO3, and listens on
    all interfaces at container port 8000.

    Returns:
        No value; serves until interrupted and closes the listening socket.

    Raises:
        ValueError: KIO_AGENT_ID is not KIO2 through KIO13.
    """
    with DummyKIOServer(
        ("0.0.0.0", 8000), os.environ.get("KIO_AGENT_ID", "KIO3")
    ) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
