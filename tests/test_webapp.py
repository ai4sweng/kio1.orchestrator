"""Tests for the Flask web UI: chat turns, JSON formatting, and file uploads."""

import json
import logging
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

    @patch("webapp.run_turn")
    def test_query_logs_plan_metadata_without_task_data(
        self, run_turn: MagicMock, client: FlaskClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log query timing and agent assignments without dumping plan inputs."""
        plan = {
            **SAMPLE_PLAN,
            "steps": [
                {
                    "step_id": "s1",
                    "agent_id": "KIO10",
                    "capability": "tinyml",
                    "task": "private-task-text",
                    "data": {"token": "private-data-value"},
                    "depends_on": [],
                }
            ],
        }
        run_turn.return_value = json.dumps(plan)
        conversation_id = client.get("/api/conversations").get_json()["active_id"]
        with caplog.at_level(logging.INFO, logger="webapp"):
            assert (
                client.post(
                    "/api/chat", json={"query": "private-user-query"}
                ).status_code
                == 200
            )
        logs = "\n".join(
            record.getMessage() for record in caplog.records if record.name == "webapp"
        )
        assert f"conversation_id={conversation_id} turn=1" in logs
        assert "Query processed:" in logs and "duration_ms=" in logs
        assert "format_duration_ms=" in logs and "response_type=workflow_plan" in logs
        assert "workflow_id='wf-e2e01'" in logs and "step_count=1" in logs
        assert "dispatch=not_started" in logs
        assert "agent_id='KIO10' capability='tinyml'" in logs
        assert all(
            secret not in logs
            for secret in (
                "private-task-text",
                "private-data-value",
                "private-user-query",
            )
        )

    @pytest.mark.parametrize(
        "content", ['{"answer": 42}', "[1, 2]", '{"steps": [null, 1]}']
    )
    @patch("webapp.run_turn")
    def test_query_logging_preserves_generic_and_partial_json(
        self,
        run_turn: MagicMock,
        client: FlaskClient,
        caplog: pytest.LogCaptureFixture,
        content: str,
    ) -> None:
        """Diagnostic logging must not reject JSON that is only being displayed."""
        run_turn.return_value = content
        with caplog.at_level(logging.INFO, logger="webapp"):
            response = client.post("/api/chat", json={"query": "show JSON"})
        assert response.status_code == 200
        assert json.loads(response.get_json()["formatted"]) == json.loads(content)
        assert "Query processed:" in caplog.text

    @patch("webapp.run_turn")
    def test_malformed_metadata_does_not_dump_nested_inputs(
        self, run_turn: MagicMock, client: FlaskClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Keep nested data and control characters out of diagnostic fields."""
        private = {"secret": "private-nested-value"}
        run_turn.return_value = json.dumps(
            {
                "workflow_id": private,
                "execution_mode": private,
                "steps": [
                    {
                        "step_id": "s1\nforged-log-line",
                        "agent_id": private,
                        "capability": private,
                        "depends_on": [private],
                    }
                ],
            }
        )
        with caplog.at_level(logging.INFO, logger="webapp"):
            assert (
                client.post("/api/chat", json={"query": "show plan"}).status_code == 200
            )
        logs = "\n".join(
            record.getMessage() for record in caplog.records if record.name == "webapp"
        )
        assert "private-nested-value" not in logs
        assert "s1\\nforged-log-line" in logs
        assert "s1\nforged-log-line" not in logs

    @patch("webapp.run_turn", side_effect=OSError("inference unavailable"))
    def test_failed_query_logs_conversation_and_duration(
        self, run_turn: MagicMock, client: FlaskClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Correlate failed queries with their conversation and elapsed time."""
        conversation_id = client.get("/api/conversations").get_json()["active_id"]
        with caplog.at_level(logging.INFO, logger="webapp"):
            assert (
                client.post("/api/chat", json={"query": "make plan"}).status_code == 502
            )
        assert (
            f"Turn failed: conversation_id={conversation_id} turn=1 duration_ms="
            in caplog.text
        )
        assert "error_type=OSError" in caplog.text
        run_turn.assert_called_once()

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
        output = capsys.readouterr().out
        assert raw_response in output
        assert "build battery firmware" in output
        assert output.index("--- User query (") < output.index(
            "--- KIO1 raw response ("
        )
        assert output.index("--- end raw response ---") < output.index(
            "--- KIO1 formatted JSON ("
        )
        printed_json = (
            output.split("--- KIO1 formatted JSON (", 1)[1]
            .split("---\n", 1)[1]
            .split("\n--- end formatted JSON ---", 1)[0]
        )
        assert json.loads(printed_json) == SAMPLE_PLAN

    @patch("webapp.run_turn", return_value="not valid json")
    def test_invalid_response_still_prints_query_and_raw_output(
        self,
        run_turn: MagicMock,
        client: FlaskClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Keep the query and raw response available when JSON formatting fails."""
        response = client.post("/api/chat", json={"query": "show broken output"})
        assert response.get_json()["raw"] == "not valid json"
        output = capsys.readouterr().out
        assert "show broken output" in output and "not valid json" in output
        assert output.index("--- User query (") < output.index(
            "--- KIO1 raw response ("
        )
        assert "--- KIO1 formatted JSON (" not in output
        run_turn.assert_called_once()

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

    @pytest.mark.parametrize("content", ["A" * 90, 42, "  multiple\n spaces  "])
    def test_summary_skips_corrupt_lines_and_limits_content(
        self, tmp_path: Path, content: str | int
    ) -> None:
        """Build safe titles without returning message bodies or prompt data."""
        conversation_id = generate_session_id()
        path = tmp_path / f"chat_{conversation_id}.jsonl"
        path.write_text(
            "invalid json\n[]\n"
            + json.dumps({"role": "user", "content": content})
            + "\n"
        )
        summary = webapp._conversation_summary(path)
        assert summary["id"] == conversation_id
        assert len(str(summary["title"])) <= 72
        assert "messages" not in summary
        if isinstance(content, int):
            assert summary["title"] == "New conversation"
        elif len(content) > 72:
            assert str(summary["title"]).endswith("...")
        else:
            assert summary["title"] == "multiple spaces"

    def test_path_helper_accepts_only_existing_regular_chats(
        self, tmp_path: Path
    ) -> None:
        """Resolve valid existing chat IDs and reject absent or malformed IDs."""
        conversation_id = generate_session_id()
        assert webapp._conversation_path(tmp_path, conversation_id) is None
        assert webapp._conversation_path(tmp_path, "../outside") is None
        path = create_chat_file(str(tmp_path), "prompt", conversation_id)
        assert webapp._conversation_path(tmp_path, conversation_id) == path

    @patch("webapp._conversation_summary", side_effect=OSError("unreadable"))
    def test_listing_skips_unreadable_history(
        self, summary: MagicMock, client: FlaskClient
    ) -> None:
        """An unreadable saved chat must not break the history endpoint."""
        response = client.get("/api/conversations")
        assert response.status_code == 200
        assert response.get_json()["conversations"] == []
        summary.assert_called_once()

    @patch("webapp.create_chat_file", side_effect=OSError("not writable"))
    def test_create_storage_failure_keeps_active_chat(
        self, create: MagicMock, client: FlaskClient
    ) -> None:
        """Do not change the active chat if creating its replacement fails."""
        active_id = client.get("/api/conversations").get_json()["active_id"]
        assert client.post("/api/conversations").status_code == 500
        assert client.get("/api/conversations").get_json()["active_id"] == active_id
        create.assert_called_once()

    @patch("webapp.load_messages", side_effect=ValueError("corrupt"))
    def test_activate_corrupt_history_returns_json_error(
        self, load: MagicMock, client: FlaskClient
    ) -> None:
        """Return a controlled error for a valid ID whose history is corrupt."""
        active_id = client.get("/api/conversations").get_json()["active_id"]
        response = client.post(f"/api/conversations/{active_id}/activate")
        assert response.status_code == 500
        assert "error" in response.get_json()
        load.assert_called_once()

    def test_delete_active_conversation_creates_empty_replacement(
        self, client: FlaskClient, tmp_path: Path
    ) -> None:
        """Keep the active session writable after deleting its history."""
        original_id = client.get("/api/conversations").get_json()["active_id"]
        response = client.delete(f"/api/conversations/{original_id}")
        assert response.status_code == 200
        active_id = response.get_json()["active_id"]
        assert active_id != original_id
        assert not (tmp_path / "chats" / f"chat_{original_id}.jsonl").exists()
        listing = client.get("/api/conversations").get_json()
        assert [item["id"] for item in listing["conversations"]] == [active_id]
        assert (
            client.post(f"/api/conversations/{active_id}/activate").get_json()[
                "messages"
            ]
            == []
        )

    def test_delete_inactive_conversation_preserves_active(
        self, client: FlaskClient
    ) -> None:
        """Deleting an older chat must not change the active conversation."""
        original_id = client.get("/api/conversations").get_json()["active_id"]
        active_id = client.post("/api/conversations").get_json()["id"]
        response = client.delete(f"/api/conversations/{original_id}")
        assert response.status_code == 200
        assert response.get_json()["active_id"] == active_id
        assert client.delete(f"/api/conversations/{original_id}").status_code == 404

    def test_delete_rejects_invalid_id(self, client: FlaskClient) -> None:
        """Reject IDs outside the persisted-chat naming convention."""
        assert client.delete("/api/conversations/not-a-chat").status_code == 404

    def test_delete_storage_failure_preserves_active_chat(
        self, client: FlaskClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Do not replace session state when deleting its original file fails."""
        original_id = client.get("/api/conversations").get_json()["active_id"]
        original_file = tmp_path / "chats" / f"chat_{original_id}.jsonl"
        original_unlink = Path.unlink

        def deny_original(path: Path, missing_ok: bool = False) -> None:
            """Simulate a permission failure only for the target conversation."""
            if path == original_file:
                raise PermissionError("test permission failure")
            original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", deny_original)
        response = client.delete(f"/api/conversations/{original_id}")
        assert response.status_code == 500
        assert client.get("/api/conversations").get_json()["active_id"] == original_id
        assert list((tmp_path / "chats").glob("chat_*.jsonl")) == [original_file]

    def test_delete_rejects_symlink(self, client: FlaskClient, tmp_path: Path) -> None:
        """Never delete a conversation through a symlink."""
        target = tmp_path / "outside.jsonl"
        target.write_text("protected")
        conversation_id = generate_session_id()
        (tmp_path / "chats" / f"chat_{conversation_id}.jsonl").symlink_to(target)
        assert client.delete(f"/api/conversations/{conversation_id}").status_code == 404
        assert target.read_text() == "protected"
        assert all(
            item["id"] != conversation_id
            for item in client.get("/api/conversations").get_json()["conversations"]
        )

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


class TestWorkflowRoute:
    """Tests for explicit, opt-in container execution."""

    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (ValueError("invalid plan"), 400),
            (webapp.WorkflowConfigurationError("invalid registry"), 503),
        ],
    )
    def test_dispatch_errors_release_execution_lock(
        self,
        client: FlaskClient,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        status: int,
    ) -> None:
        """Failed requests return JSON and allow the next execution attempt."""
        monkeypatch.setenv("KIO1_AGENT_CONFIG", "agents.json")
        with patch("webapp.load_agent_registry", return_value={}), patch(
            "webapp.dispatch_workflow", side_effect=error
        ):
            for attempt in range(2):
                response = client.post(
                    "/api/workflows", json={"approved": True, "plan": SAMPLE_PLAN}
                )
                assert response.status_code == status
                assert "error" in response.get_json()

    def test_concurrent_workflow_is_rejected(
        self, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reject a second workflow while the first holds its execution lock."""
        monkeypatch.setenv("KIO1_AGENT_CONFIG", "agents.json")

        def dispatch(plan: dict, registry: dict) -> dict:
            """Submit a second request while the outer dispatcher is running."""
            nested = client.post(
                "/api/workflows", json={"approved": True, "plan": plan}
            )
            assert nested.status_code == 409
            return {"status": "succeeded", "steps": []}

        with patch("webapp.load_agent_registry", return_value={}), patch(
            "webapp.dispatch_workflow", side_effect=dispatch
        ):
            assert (
                client.post(
                    "/api/workflows", json={"approved": True, "plan": SAMPLE_PLAN}
                ).status_code
                == 200
            )

    def test_dispatch_disabled_by_default(
        self, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Generating plans must not require or enable agent connectivity."""
        monkeypatch.delenv("KIO1_AGENT_CONFIG", raising=False)
        assert client.post("/api/workflows", json={"approved": True}).status_code == 503

    @patch("webapp.dispatch_workflow")
    def test_dispatch_requires_approval_and_trusted_registry(
        self,
        dispatch: MagicMock,
        client: FlaskClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only explicitly approved plans reach the configured dispatcher."""
        registry_path = tmp_path / "agents.json"
        registry_path.write_text('{"KIO7": {"endpoint": "http://kio7:8000/tasks"}}')
        monkeypatch.setenv("KIO1_AGENT_CONFIG", str(registry_path))
        assert (
            client.post("/api/workflows", json={"plan": SAMPLE_PLAN}).status_code == 400
        )
        dispatch.assert_not_called()
        dispatch.return_value = {
            "workflow_id": "wf-test",
            "status": "succeeded",
            "steps": [],
        }
        response = client.post(
            "/api/workflows", json={"approved": True, "plan": SAMPLE_PLAN}
        )
        assert response.status_code == 200
        assert response.get_json()["status"] == "succeeded"
        dispatch.assert_called_once_with(
            SAMPLE_PLAN, {"KIO7": {"endpoint": "http://kio7:8000/tasks"}}
        )

    def test_dispatch_rejects_malformed_and_oversized_requests(
        self, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Return JSON validation errors without entering the dispatcher."""
        monkeypatch.setenv("KIO1_AGENT_CONFIG", "unused.json")
        assert client.post("/api/workflows", json=[]).status_code == 400
        assert (
            client.post("/api/workflows", data=b"x" * (1024 * 1024 + 1)).status_code
            == 413
        )


class TestUploadRoute:
    """Tests for `POST /api/upload`."""

    def test_rejects_filename_without_safe_characters(
        self, client: FlaskClient
    ) -> None:
        """Reject names that become empty after secure filename normalization."""
        response = client.post("/api/upload", data={"file": (BytesIO(b"data"), "!!!")})
        assert response.status_code == 400

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
