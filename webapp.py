"""Flask web UI: a messenger-style chat backed by the Ollama orchestrator.

Replaces the terminal REPL in `main.py` with a browser UI. The UI also
accepts model/dataset/file uploads, saved under `assets/<category>/`.
"""

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from formatter import format_json
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.utils import secure_filename

from chat_history import create_chat_file, load_messages
from config_loader import Config
from main import run_turn
from session_logger import generate_session_id, measure_duration
from telemetry import trace_operation
from workflow_planner import (
    WorkflowConfigurationError,
    dispatch_workflow,
    load_agent_registry,
)

logger = logging.getLogger("webapp")

ASSETS_DIRECTORY = Path("assets")

# Dataset/checkpoint uploads can be large; this bounds worst-case disk and
# memory usage from a single upload rather than leaving it unbounded.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

# Maps the UI's upload category to its assets/ subfolder. Looked up through
# this fixed mapping (never the raw request value) so a crafted "category"
# field cannot be used to traverse outside assets/.
UPLOAD_SUBDIRECTORIES = {
    "model": "models",
    "dataset": "datasets",
    "file": "files",
}
DEFAULT_UPLOAD_CATEGORY = "file"
CONVERSATION_ID_PATTERN = re.compile(r"\d{8}_\d{6}_[0-9a-f]{8}\Z")


def _conversation_path(chat_directory: Path, conversation_id: str) -> Path | None:
    """Return an existing, valid conversation path without following symlinks.

    Args:
        chat_directory: Directory containing saved chat files.
        conversation_id: Timestamp-and-UUID identifier to validate and resolve.

    Returns:
        The existing regular chat-file path, or None for invalid or missing IDs.
    """
    if not CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
        return None

    path = chat_directory / f"chat_{conversation_id}.jsonl"
    if path.is_symlink() or not path.is_file():
        return None
    return path


def _conversation_summary(chat_file: Path) -> dict[str, str | int]:
    """Build a safe, content-limited summary for the history selector.

    Args:
        chat_file: Saved JSONL conversation to inspect without returning messages.

    Returns:
        The conversation ID, a title of at most 72 characters, and UTC update time.

    Raises:
        OSError: The chat file or its metadata cannot be read.
    """
    first_query = ""
    with chat_file.open(encoding="utf-8") as history_file:
        for line in history_file:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                first_query = content
            break

    title = " ".join(first_query.split()) or "New conversation"
    if len(title) > 72:
        title = f"{title[:69].rstrip()}..."

    return {
        "id": chat_file.stem.removeprefix("chat_"),
        "title": title,
        "updated_at": datetime.fromtimestamp(
            chat_file.stat().st_mtime, timezone.utc
        ).isoformat(),
    }


class _ChatSession:
    """Mutable state for the single active chat conversation.

    The web UI serves one operator at a time, mirroring the single-session
    terminal REPL it replaces, so a shared session guarded by a lock is
    enough to keep turn numbering and chat-file writes consistent across
    concurrent requests.
    """

    def __init__(
        self, config: Config, system_prompt: str, chat_file: Path, session_id: str
    ) -> None:
        """Initialize shared chat state and its request-serialization lock.

        Args:
            config: Application and model settings.
            system_prompt: Rendered system prompt for the active session.
            chat_file: Existing history file used for subsequent turns.
            session_id: Identifier used for chat filenames and trace correlation.

        Returns:
            None. Initializes the active session at turn zero with an unlocked
            request-serialization lock.
        """
        self.config = config
        self.system_prompt = system_prompt
        self.chat_file = chat_file
        self.session_id = session_id
        self.turn = 0
        self.lock = threading.Lock()


def create_app(
    config: Config,
    system_prompt: str,
    chat_file: Path,
    session_id: str,
) -> Flask:
    """Build the Flask application serving the chat UI and upload endpoint.

    Args:
        config: Application configuration.
        system_prompt: The system prompt text.
        chat_file: Path to the session's chat history file.
        session_id: Shared session id used for tracing.

    Returns:
        A configured Flask application.
    """
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    session = _ChatSession(config, system_prompt, chat_file, session_id)
    workflow_lock = threading.Lock()

    @app.get("/")
    def index() -> str:
        """Render the operator's chat page.

        Returns:
            HTML with the configured model name and workflow-enabled flag.
        """
        return render_template(
            "index.html",
            model_name=session.config.model,
            workflow_enabled=bool(os.environ.get("KIO1_AGENT_CONFIG")),
        )

    @app.post("/api/workflows")
    def execute_workflow() -> tuple[Response, int] | Response:
        """Dispatch an explicitly approved plan using operator-configured routes.

        Returns:
            JSON workflow outcomes, or an error with HTTP 400 for invalid input,
            409 for concurrent execution, 413 for size, or 503 for configuration.
        """
        registry_path = os.environ.get("KIO1_AGENT_CONFIG")
        if not registry_path:
            return (
                jsonify(
                    {"error": "Workflow dispatch is disabled. Set KIO1_AGENT_CONFIG."}
                ),
                503,
            )
        if request.content_length is None or request.content_length > 1024 * 1024:
            return jsonify({"error": "Workflow request must not exceed 1 MiB."}), 413
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get("approved") is not True:
            return (
                jsonify(
                    {"error": "An object with approved=true and plan is required."}
                ),
                400,
            )
        if not workflow_lock.acquire(blocking=False):
            return jsonify({"error": "Another workflow is running."}), 409
        try:
            registry = load_agent_registry(Path(registry_path))
            with trace_operation("kio1.workflow") as span:
                result = dispatch_workflow(payload.get("plan"), registry)
                span.set_attribute("workflow.status", result["status"])
            return jsonify(result)
        except WorkflowConfigurationError as error:
            return jsonify({"error": str(error)}), 503
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        finally:
            workflow_lock.release()

    @app.get("/api/conversations")
    def conversations() -> Response:
        """List readable saved conversations in newest-first order.

        Returns:
            JSON containing active_id and content-limited conversation summaries.
        """
        summaries: list[dict[str, str | int]] = []
        with session.lock:
            for chat_file in session.chat_file.parent.glob("chat_*.jsonl"):
                conversation_id = chat_file.stem.removeprefix("chat_")
                if (
                    _conversation_path(session.chat_file.parent, conversation_id)
                    is None
                ):
                    continue
                try:
                    summaries.append(_conversation_summary(chat_file))
                except (OSError, ValueError):
                    logger.warning(
                        "Skipping unreadable conversation: id=%s", conversation_id
                    )

            summaries.sort(key=lambda item: str(item["updated_at"]), reverse=True)
            active_id = session.chat_file.stem.removeprefix("chat_")

        return jsonify({"active_id": active_id, "conversations": summaries})

    @app.post("/api/conversations")
    def create_conversation() -> tuple[Response, int] | Response:
        """Create and activate a conversation with the current system prompt.

        Returns:
            JSON ID and empty messages with HTTP 201, or a storage error with 500.
        """
        with session.lock:
            conversation_id = generate_session_id()
            try:
                chat_file = create_chat_file(
                    str(session.chat_file.parent),
                    session.system_prompt,
                    conversation_id,
                )
            except OSError:
                logger.exception("Could not create conversation")
                return jsonify({"error": "Could not create a new conversation."}), 500

            session.chat_file = chat_file
            session.session_id = conversation_id
            session.turn = 0

        return jsonify({"id": conversation_id, "messages": []}), 201

    @app.post("/api/conversations/<conversation_id>/activate")
    def activate_conversation(conversation_id: str) -> tuple[Response, int] | Response:
        """Restore a saved conversation and its turn counter.

        Args:
            conversation_id: Saved chat identifier from the URL path.

        Returns:
            JSON ID and user/assistant messages, or an error with HTTP 404 for
            missing/invalid IDs or 500 for unreadable history.
        """
        with session.lock:
            chat_file = _conversation_path(session.chat_file.parent, conversation_id)
            if chat_file is None:
                return jsonify({"error": "Conversation not found."}), 404

            try:
                messages = load_messages(chat_file)
            except (OSError, ValueError):
                logger.exception("Could not load conversation: id=%s", conversation_id)
                return jsonify({"error": "Could not load the conversation."}), 500

            session.chat_file = chat_file
            session.session_id = conversation_id
            session.turn = sum(message.get("role") == "user" for message in messages)

        return jsonify({"id": conversation_id, "messages": messages})

    @app.delete("/api/conversations/<conversation_id>")
    def delete_conversation(conversation_id: str) -> tuple[Response, int] | Response:
        """Delete one persisted chat, replacing it first if it is active.

        Args:
            conversation_id: Saved chat identifier from the URL path.

        Returns:
            JSON deleted_id and active_id, or an error with HTTP 404 for
            missing/invalid IDs or 500 for storage failures.
        """
        with session.lock:
            chat_file = _conversation_path(session.chat_file.parent, conversation_id)
            if chat_file is None:
                return jsonify({"error": "Conversation not found."}), 404

            replacement = None
            replacement_id = None
            try:
                if chat_file == session.chat_file:
                    replacement_id = generate_session_id()
                    replacement = create_chat_file(
                        str(chat_file.parent), session.system_prompt, replacement_id
                    )
                chat_file.unlink()
            except OSError:
                if replacement is not None:
                    replacement.unlink(missing_ok=True)
                logger.exception(
                    "Could not delete conversation: id=%s", conversation_id
                )
                return jsonify({"error": "Could not delete the conversation."}), 500

            if replacement is not None and replacement_id is not None:
                session.chat_file = replacement
                session.session_id = replacement_id
                session.turn = 0
            active_id = session.session_id

        logger.info("Conversation deleted: id=%s", conversation_id)
        return jsonify({"deleted_id": conversation_id, "active_id": active_id})

    @app.post("/api/chat")
    def chat() -> tuple[Response, int] | Response:
        """Run and persist a model turn in the active conversation.

        Returns:
            JSON formatted response, raw text with a formatting error, or an
            error with HTTP 400 for blank input or 502 for inference failure.
        """
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("query", "")).strip()

        if not query:
            return jsonify({"error": "Query must not be empty."}), 400

        with session.lock:
            session.turn += 1
            turn = session.turn
            conversation_id = session.session_id
            logger.info(
                "Turn started: conversation_id=%s turn=%d", conversation_id, turn
            )

            try:
                with measure_duration() as turn_elapsed:
                    content = run_turn(
                        config=session.config,
                        system_prompt=session.system_prompt,
                        chat_file=session.chat_file,
                        session_id=session.session_id,
                        turn=turn,
                        query=query,
                    )
            except Exception as error:
                logger.exception(
                    "Turn failed: conversation_id=%s turn=%d duration_ms=%d error_type=%s",
                    conversation_id,
                    turn,
                    turn_elapsed(),
                    type(error).__name__,
                )
                return jsonify({"error": str(error)}), 502

        logger.info(
            "Turn completed: conversation_id=%s turn=%d duration_ms=%d",
            conversation_id,
            turn,
            turn_elapsed(),
        )
        terminal_output = (
            f"\n--- User query ({conversation_id}, turn {turn}) ---\n"
            f"{query}\n--- end user query ---\n"
            f"\n--- KIO1 raw response ({conversation_id}, turn {turn}) ---\n"
            f"{content}\n--- end raw response ---\n"
        )

        try:
            with measure_duration() as format_elapsed:
                formatted = format_json(content)
        except ValueError as error:
            print(terminal_output, flush=True)
            logger.warning(
                "Response formatting failed: conversation_id=%s turn=%d duration_ms=%d error_type=%s",
                conversation_id,
                turn,
                turn_elapsed(),
                type(error).__name__,
            )
            return jsonify({"raw": content, "error": str(error)})

        print(
            f"{terminal_output}\n--- KIO1 formatted JSON ({conversation_id}, turn {turn}) ---\n"
            f"{formatted}\n--- end formatted JSON ---\n",
            flush=True,
        )

        parsed = json.loads(formatted)
        is_plan = isinstance(parsed, dict) and isinstance(parsed.get("steps"), list)
        logger.info(
            "Query processed: conversation_id=%s turn=%d status=succeeded "
            "duration_ms=%d format_duration_ms=%d response_type=%s",
            conversation_id,
            turn,
            turn_elapsed(),
            format_elapsed(),
            "workflow_plan" if is_plan else "json",
        )
        if is_plan:
            workflow_id = parsed.get("workflow_id")
            execution_mode = parsed.get("execution_mode")
            logger.info(
                "Workflow plan generated: conversation_id=%s turn=%d workflow_id=%r "
                "execution_mode=%r step_count=%d dispatch=not_started",
                conversation_id,
                turn,
                workflow_id[:128] if isinstance(workflow_id, str) else None,
                execution_mode[:32] if isinstance(execution_mode, str) else None,
                len(parsed["steps"]),
            )
            for step in parsed["steps"]:
                if isinstance(step, dict):
                    identifiers = [
                        value[:128] if isinstance(value, str) else None
                        for value in (
                            workflow_id,
                            step.get("step_id"),
                            step.get("agent_id"),
                            step.get("capability"),
                        )
                    ]
                    dependencies = step.get("depends_on", [])
                    dependency_ids = (
                        [
                            value[:128]
                            for value in dependencies[:64]
                            if isinstance(value, str)
                        ]
                        if isinstance(dependencies, list)
                        else []
                    )
                    logger.info(
                        "Workflow plan step: conversation_id=%s turn=%d workflow_id=%r "
                        "step_id=%r agent_id=%r capability=%r depends_on=%r",
                        conversation_id,
                        turn,
                        *identifiers,
                        dependency_ids,
                    )

        return jsonify({"formatted": formatted})

    @app.post("/api/upload")
    def upload() -> tuple[Response, int] | Response:
        """Save an uploaded file beneath its fixed asset-category directory.

        Returns:
            JSON relative filename, or an error with HTTP 400 for invalid input,
            413 for oversized requests, or 500 when the file cannot be saved.
        """
        uploaded_file = request.files.get("file")

        if uploaded_file is None or not uploaded_file.filename:
            return jsonify({"error": "No file provided."}), 400

        safe_name = secure_filename(uploaded_file.filename)
        if not safe_name:
            return jsonify({"error": "Invalid file name."}), 400

        category = request.form.get("category", DEFAULT_UPLOAD_CATEGORY)
        subdirectory = UPLOAD_SUBDIRECTORIES.get(
            category, UPLOAD_SUBDIRECTORIES[DEFAULT_UPLOAD_CATEGORY]
        )

        # Prefixed with a short id so re-uploading a same-named file never
        # silently overwrites a previous one.
        stored_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        relative_path = f"{subdirectory}/{stored_name}"
        destination = ASSETS_DIRECTORY / relative_path

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            uploaded_file.save(destination)
        except OSError as error:
            logger.exception("File upload failed: name=%s", safe_name)
            return (
                jsonify(
                    {
                        "error": (
                            f"Could not save file on the server: {error}. "
                            "Check that assets/ is writable."
                        )
                    }
                ),
                500,
            )

        logger.info(
            "File uploaded: name=%s category=%s path=%s",
            safe_name,
            category,
            destination,
        )
        return jsonify({"filename": relative_path})

    return app
