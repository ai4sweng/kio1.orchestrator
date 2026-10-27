"""Tests for the Flask web UI: chat turns, JSON formatting, and file uploads."""

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask.testing import FlaskClient

import webapp
from chat_history import create_chat_file
from config_loader import Config
from session_logger import generate_session_id


def make_config(
    model: str = "test-model",
    temperature: float = 0.1,
    request_timeout: float = 5,
    keep_alive: int = -1,
    max_output_tokens: int = 1024,
    endpoint: str = "http://localhost:11434",
    context_window_size: int = 16384,
) -> Config:
    """Create a configuration for web UI tests.

    Returns:
        A `Config` instance for use in tests.
    """
    return Config(
        model=model,
        prompt_path="prompt.txt",
        chat_directory="chats",
        temperature=temperature,
        request_timeout=request_timeout,
        keep_alive=keep_alive,
        max_output_tokens=max_output_tokens,
        endpoint=endpoint,
        context_window_size=context_window_size,
    )


def _mock_ollama_response(content: str) -> MagicMock:
    """Build a mocked `urlopen` context manager returning an Ollama chat reply.

    Args:
        content: The assistant message content Ollama should appear to return.

    Returns:
        A mock usable as `urlopen`'s return value.
    """
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        {"message": {"content": content}}
    ).encode("utf-8")
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FlaskClient:
    """Build a Flask test client backed by a scratch chat file and assets dir."""
    monkeypatch.setattr(webapp, "ASSETS_DIRECTORY", tmp_path / "assets")

    session_id = generate_session_id()
    chat_file = create_chat_file(str(tmp_path / "chats"), "You are KIO1.", session_id)

    app = webapp.create_app(
        config=make_config(),
        system_prompt="You are KIO1.",
        chat_file=chat_file,
        session_id=session_id,
    )
    app.testing = True
    return app.test_client()


SAMPLE_PLAN = {
    "workflow_id": "wf-e2e01",
    "execution_mode": "mixed",
    "steps": [{"step_id": "s1", "agent_id": "KIO10", "task": "a"}],
    "explanation": "e2e",
}


class TestIndexRoute:
    """Tests for the page serving the chat UI."""

    def test_index_serves_html(self, client: FlaskClient) -> None:
        """Verify the root route returns the chat UI page."""
        response = client.get("/")

        assert response.status_code == 200
        assert b"<html" in response.data
        assert b"What can I help with?" in response.data
        assert b"test-model" in response.data
        assert b'aria-label="Conversation"' in response.data


class TestChatRoute:
    """Tests for `POST /api/chat`."""

    @patch("main.urllib.request.urlopen")
    def test_returns_formatted_json(
        self,
        mock_urlopen: MagicMock,
        client: FlaskClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify the model's JSON reply comes back pretty-printed and unchanged."""
        raw_response = json.dumps(SAMPLE_PLAN)
        mock_urlopen.return_value = _mock_ollama_response(raw_response)

        response = client.post("/api/chat", json={"query": "build battery firmware"})

        assert response.status_code == 200
        data = response.get_json()
        assert json.loads(data["formatted"]) == SAMPLE_PLAN
        assert raw_response in capsys.readouterr().out

    @patch("main.urllib.request.urlopen")
    def test_chat_history_is_persisted(
        self, mock_urlopen: MagicMock, client: FlaskClient, tmp_path: Path
    ) -> None:
        """Verify the user/assistant turn is appended to the chat file."""
        mock_urlopen.return_value = _mock_ollama_response(json.dumps(SAMPLE_PLAN))

        client.post("/api/chat", json={"query": "build battery firmware"})

        chat_lines = (
            next((tmp_path / "chats").glob("chat_*.jsonl")).read_text().splitlines()
        )
        roles = [json.loads(line)["role"] for line in chat_lines]
        assert roles == ["system", "user", "assistant"]
        assert json.loads(chat_lines[1])["content"] == "build battery firmware"
        assert json.loads(json.loads(chat_lines[2])["content"]) == SAMPLE_PLAN

    @patch("main.urllib.request.urlopen")
    def test_turn_count_increments_across_requests(
        self, mock_urlopen: MagicMock, client: FlaskClient, tmp_path: Path
    ) -> None:
        """Verify successive queries keep extending the same chat history."""
        mock_urlopen.return_value = _mock_ollama_response(json.dumps(SAMPLE_PLAN))

        client.post("/api/chat", json={"query": "first query"})
        client.post("/api/chat", json={"query": "second query"})

        chat_lines = (
            next((tmp_path / "chats").glob("chat_*.jsonl")).read_text().splitlines()
        )
        assert [json.loads(line)["role"] for line in chat_lines] == [
            "system",
            "user",
            "assistant",
            "user",
            "assistant",
        ]

    @patch("main.urllib.request.urlopen")
    def test_invalid_json_response_returns_raw_and_error(
        self, mock_urlopen: MagicMock, client: FlaskClient
    ) -> None:
        """Verify a non-JSON reply is reported as an error alongside the raw text."""
        mock_urlopen.return_value = _mock_ollama_response("not valid json")

        response = client.post("/api/chat", json={"query": "build battery firmware"})

        assert response.status_code == 200
        data = response.get_json()
        assert data["raw"] == "not valid json"
        assert "error" in data

    def test_blank_query_is_rejected(self, client: FlaskClient) -> None:
        """Verify an empty query is rejected before reaching Ollama."""
        response = client.post("/api/chat", json={"query": "   "})

        assert response.status_code == 400
        assert "error" in response.get_json()

    @patch("main.urllib.request.urlopen")
    def test_ollama_failure_returns_502(
        self, mock_urlopen: MagicMock, client: FlaskClient
    ) -> None:
        """Verify an upstream Ollama failure is reported as a 502, not a crash."""
        mock_urlopen.side_effect = OSError("connection refused")

        response = client.post("/api/chat", json={"query": "build battery firmware"})

        assert response.status_code == 502
        assert "error" in response.get_json()


class TestConversationRoutes:
    """Tests for creating, listing, and activating persisted chats."""

    @patch("main.urllib.request.urlopen")
    def test_create_and_activate_conversation_keeps_histories_separate(
        self,
        mock_urlopen: MagicMock,
        client: FlaskClient,
        tmp_path: Path,
    ) -> None:
        mock_urlopen.return_value = _mock_ollama_response(json.dumps(SAMPLE_PLAN))

        client.post("/api/chat", json={"query": "first conversation"})
        original = client.get("/api/conversations").get_json()
        original_id = original["active_id"]
        original_summary = next(
            item for item in original["conversations"] if item["id"] == original_id
        )
        assert original_summary["title"] == "first conversation"
        assert "messages" not in original_summary

        created = client.post("/api/conversations")
        assert created.status_code == 201
        new_id = created.get_json()["id"]
        assert new_id != original_id
        assert created.get_json()["messages"] == []

        new_listing = client.get("/api/conversations").get_json()
        assert new_listing["active_id"] == new_id
        assert len(new_listing["conversations"]) == 2
        assert all("system" not in item for item in new_listing["conversations"])

        opened = client.post(f"/api/conversations/{original_id}/activate")
        assert opened.status_code == 200
        restored_messages = opened.get_json()["messages"]
        assert [message["role"] for message in restored_messages] == [
            "user",
            "assistant",
        ]
        assert restored_messages[0]["content"] == "first conversation"

        client.post("/api/chat", json={"query": "continue first conversation"})
        original_file = tmp_path / "chats" / f"chat_{original_id}.jsonl"
        new_file = tmp_path / "chats" / f"chat_{new_id}.jsonl"
        original_messages = [
            json.loads(line) for line in original_file.read_text().splitlines()
        ]
        new_messages = [json.loads(line) for line in new_file.read_text().splitlines()]

        assert [
            message["content"]
            for message in original_messages
            if message["role"] == "user"
        ] == [
            "first conversation",
            "continue first conversation",
        ]
        assert [message["role"] for message in new_messages] == ["system"]

    def test_activate_rejects_invalid_conversation_id(
        self, client: FlaskClient
    ) -> None:
        response = client.post("/api/conversations/not-a-chat/activate")

        assert response.status_code == 404
        assert response.get_json() == {"error": "Conversation not found."}


class TestUploadRoute:
    """Tests for `POST /api/upload`."""

    def test_saves_file_to_assets_directory(
        self, client: FlaskClient, tmp_path: Path
    ) -> None:
        """Verify an uploaded file with no category defaults to assets/files/."""
        response = client.post(
            "/api/upload",
            data={"file": (BytesIO(b"weights-bytes"), "model.ckpt")},
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        filename = response.get_json()["filename"]
        assert filename.startswith("files/")
        saved_path = tmp_path / "assets" / filename
        assert saved_path.exists()
        assert saved_path.read_bytes() == b"weights-bytes"

    @pytest.mark.parametrize(
        ("category", "subdirectory"),
        [("model", "models"), ("dataset", "datasets"), ("file", "files")],
    )
    def test_saves_file_under_category_subdirectory(
        self, client: FlaskClient, tmp_path: Path, category: str, subdirectory: str
    ) -> None:
        """Verify each upload category is saved under its own assets/ subfolder."""
        response = client.post(
            "/api/upload",
            data={
                "file": (BytesIO(b"bytes"), "thing.bin"),
                "category": category,
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        filename = response.get_json()["filename"]
        assert filename.startswith(f"{subdirectory}/")
        assert (tmp_path / "assets" / filename).exists()

    def test_unknown_category_falls_back_to_files(self, client: FlaskClient) -> None:
        """Verify an unrecognized category falls back to the default subfolder."""
        response = client.post(
            "/api/upload",
            data={
                "file": (BytesIO(b"bytes"), "thing.bin"),
                "category": "not-a-real-category",
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        assert response.get_json()["filename"].startswith("files/")

    def test_sanitizes_path_traversal_filename(
        self, client: FlaskClient, tmp_path: Path
    ) -> None:
        """Verify a malicious filename cannot escape the assets directory."""
        response = client.post(
            "/api/upload",
            data={"file": (BytesIO(b"data"), "../../etc/passwd")},
            content_type="multipart/form-data",
        )

        assert response.status_code == 200
        filename = response.get_json()["filename"]
        saved_path = tmp_path / "assets" / filename
        assert saved_path.resolve().parent == (tmp_path / "assets" / "files").resolve()

    def test_rejects_missing_file(self, client: FlaskClient) -> None:
        """Verify a request with no file part is rejected."""
        response = client.post(
            "/api/upload", data={}, content_type="multipart/form-data"
        )

        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_write_failure_returns_500_with_error_json(
        self, client: FlaskClient
    ) -> None:
        """Verify a filesystem error (e.g. permission denied) is reported as JSON.

        Mirrors a real misconfiguration: a Docker bind-mounted assets/ owned
        by a different user than the container process.
        """
        with patch(
            "werkzeug.datastructures.FileStorage.save",
            side_effect=PermissionError("denied"),
        ):
            response = client.post(
                "/api/upload",
                data={"file": (BytesIO(b"data"), "model.ckpt")},
                content_type="multipart/form-data",
            )

        assert response.status_code == 500
        assert "error" in response.get_json()
