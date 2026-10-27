"""Flask web UI: a messenger-style chat backed by the Ollama orchestrator.

Replaces the terminal REPL in `main.py` with a browser UI. The UI also
accepts model/dataset/file uploads, saved under `assets/<category>/`.
"""

import json
import logging
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
    """Return an existing, valid conversation path without following symlinks."""
    if not CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
        return None

    path = chat_directory / f"chat_{conversation_id}.jsonl"
    if path.is_symlink() or not path.is_file():
        return None
    return path


def _conversation_summary(chat_file: Path) -> dict[str, str | int]:
    """Build a safe, content-limited summary for the history selector."""
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
        self,
        config: Config,
        system_prompt: str,
        chat_file: Path,
        session_id: str,
    ) -> None:
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

    @app.get("/")
    def index() -> str:
        return render_template("index.html", model_name=session.config.model)

    @app.get("/api/conversations")
    def conversations() -> Response:
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

    @app.post("/api/chat")
    def chat() -> tuple[Response, int] | Response:
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("query", "")).strip()

        if not query:
            return jsonify({"error": "Query must not be empty."}), 400

        with session.lock:
            session.turn += 1
            turn = session.turn
            logger.info("Turn started: turn=%d", turn)

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
                    "Turn failed: turn=%d duration_ms=%d", turn, turn_elapsed()
                )
                return jsonify({"error": str(error)}), 502

        logger.info("Turn completed: turn=%d duration_ms=%d", turn, turn_elapsed())
        print(
            f"\n--- KIO1 raw response ({session.session_id}, turn {turn}) ---\n"
            f"{content}\n--- end raw response ---\n",
            flush=True,
        )

        try:
            formatted = format_json(content)
        except ValueError as error:
            logger.warning("Response formatting failed: turn=%d error=%s", turn, error)
            return jsonify({"raw": content, "error": str(error)})

        return jsonify({"formatted": formatted})

    @app.post("/api/upload")
    def upload() -> tuple[Response, int] | Response:
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
