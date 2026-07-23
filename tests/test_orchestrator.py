import json
from formatter import format_json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chat_history import (
    append_assistant_message,
    append_user_message,
    create_chat_file,
    load_messages,
)
from config_loader import Config, load_config
from ollama_client import extract_content, preload_model, send_request
from prompt_loader import load_prompt


class TestConfigLoader:
    """Tests for configuration loading."""

    def test_load_config_reads_all_fields(self, tmp_path: Path) -> None:
        """Verify all config fields are loaded correctly.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        config_data = {
            "model": "test-model",
            "ollama_endpoint": "http://localhost:11434",
            "prompt_path": "prompts/test.txt",
            "chat_directory": "chats",
            "temperature": 0.1,
            "request_timeout": 120,
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        config = load_config(str(config_file))

        assert config.model == "test-model"
        assert config.ollama_endpoint == "http://localhost:11434"
        assert config.prompt_path == "prompts/test.txt"
        assert config.chat_directory == "chats"
        assert config.temperature == 0.1
        assert config.request_timeout == 120
        assert config.keep_alive == -1

    def test_load_config_reads_optional_ollama_runtime_fields(
        self, tmp_path: Path
    ) -> None:
        """Verify optional Ollama runtime config fields are loaded.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        config_data = {
            "model": "test-model",
            "ollama_endpoint": "http://localhost:11434",
            "prompt_path": "prompts/test.txt",
            "chat_directory": "chats",
            "temperature": 0.1,
            "request_timeout": 120,
            "keep_alive": 600,
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        config = load_config(str(config_file))

        assert config.keep_alive == 600

    @pytest.mark.parametrize("invalid_keep_alive", [True, False, 1.5, "600", None])
    def test_load_config_rejects_non_integer_keep_alive(
        self, tmp_path: Path, invalid_keep_alive: object
    ) -> None:
        """Verify non-integer keep_alive values are rejected.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        config_data = {
            "model": "test-model",
            "ollama_endpoint": "http://localhost:11434",
            "prompt_path": "prompts/test.txt",
            "chat_directory": "chats",
            "temperature": 0.1,
            "request_timeout": 120,
            "keep_alive": invalid_keep_alive,
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with pytest.raises(ValueError, match="keep_alive must be an integer"):
            load_config(str(config_file))

    def test_load_config_missing_file_raises(self) -> None:
        """Verify that a missing config file raises an error.

        Args:
            None.

        Returns:
            None.
        """
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.json")

    def test_config_dataclass_fields(self) -> None:
        """Verify Config dataclass holds the expected values.

        Args:
            None.

        Returns:
            None.
        """
        config = Config(
            model="m",
            ollama_endpoint="http://localhost",
            prompt_path="p.txt",
            chat_directory="c",
            temperature=0.1,
            request_timeout=120,
            keep_alive=-1,
        )
        assert config.model == "m"
        assert config.ollama_endpoint == "http://localhost"
        assert config.temperature == 0.1
        assert config.request_timeout == 120
        assert config.keep_alive == -1


class TestPromptLoader:
    """Tests for prompt loading."""

    def test_load_prompt_returns_content(self, tmp_path: Path) -> None:
        """Verify prompt content is loaded and stripped.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("  You are a helpful assistant.  \n")

        result = load_prompt(str(prompt_file))

        assert result == "You are a helpful assistant."

    def test_load_prompt_missing_file_raises(self) -> None:
        """Verify that a missing prompt file raises an error.

        Args:
            None.

        Returns:
            None.
        """
        with pytest.raises(FileNotFoundError):
            load_prompt("/nonexistent/prompt.txt")


class TestChatHistory:
    """Tests for chat history storage."""

    def test_create_chat_file_creates_directory(self, tmp_path: Path) -> None:
        """Verify chat directory is created if it doesn't exist.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        chat_dir = tmp_path / "chats"
        chat_file = create_chat_file(str(chat_dir), "system prompt")

        assert chat_dir.exists()
        assert chat_file.exists()

    def test_create_chat_file_writes_system_prompt(self, tmp_path: Path) -> None:
        """Verify the system prompt is the first line of the chat file as JSONL.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        chat_dir = tmp_path / "chats"
        chat_file = create_chat_file(str(chat_dir), "You are an AI.")

        lines = chat_file.read_text().strip().split("\n")
        first = json.loads(lines[0])
        assert first == {"role": "system", "content": "You are an AI."}

    def test_create_chat_file_has_jsonl_extension(self, tmp_path: Path) -> None:
        """Verify the chat file uses the .jsonl extension.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        chat_dir = tmp_path / "chats"
        chat_file = create_chat_file(str(chat_dir), "system")

        assert chat_file.suffix == ".jsonl"

    def test_create_chat_file_unique_names(self, tmp_path: Path) -> None:
        """Verify consecutive calls produce unique filenames.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        chat_dir = tmp_path / "chats"
        file_a = create_chat_file(str(chat_dir), "system")
        file_b = create_chat_file(str(chat_dir), "system")

        assert file_a.name != file_b.name

    def test_append_user_message(self, tmp_path: Path) -> None:
        """Verify user messages are appended as JSONL.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        chat_dir = tmp_path / "chats"
        chat_file = create_chat_file(str(chat_dir), "system")
        append_user_message(chat_file, "hello")

        lines = chat_file.read_text().strip().split("\n")
        second = json.loads(lines[1])
        assert second == {"role": "user", "content": "hello"}

    def test_append_assistant_message(self, tmp_path: Path) -> None:
        """Verify assistant messages are appended as JSONL.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        chat_dir = tmp_path / "chats"
        chat_file = create_chat_file(str(chat_dir), "system")
        append_user_message(chat_file, "hello")
        append_assistant_message(chat_file, '{"reply": "hi"}')

        lines = chat_file.read_text().strip().split("\n")
        third = json.loads(lines[2])
        assert third == {"role": "assistant", "content": '{"reply": "hi"}'}

    def test_load_messages_returns_correct_roles(self, tmp_path: Path) -> None:
        """Verify messages are loaded with alternating roles.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        chat_dir = tmp_path / "chats"
        chat_file = create_chat_file(str(chat_dir), "system prompt")
        append_user_message(chat_file, "question 1")
        append_assistant_message(chat_file, "answer 1")
        append_user_message(chat_file, "question 2")

        messages = load_messages(chat_file)

        assert len(messages) == 3
        assert messages[0] == {"role": "user", "content": "question 1"}
        assert messages[1] == {"role": "assistant", "content": "answer 1"}
        assert messages[2] == {"role": "user", "content": "question 2"}

    def test_load_messages_empty_conversation(self, tmp_path: Path) -> None:
        """Verify empty conversation returns no messages.

        Args:
            tmp_path: Pytest temporary directory fixture.

        Returns:
            None.
        """
        chat_dir = tmp_path / "chats"
        chat_file = create_chat_file(str(chat_dir), "system prompt")

        messages = load_messages(chat_file)
        assert messages == []


class TestFormatter:
    """Tests for JSON formatting."""

    def test_format_json_pretty_prints(self) -> None:
        """Verify JSON is formatted with 2-space indentation.

        Args:
            None.

        Returns:
            None.
        """
        raw = '{"key":"value","num":42}'
        result = format_json(raw)

        expected = '{\n  "key": "value",\n  "num": 42\n}'
        assert result == expected

    def test_format_json_nested(self) -> None:
        """Verify nested JSON is properly formatted.

        Args:
            None.

        Returns:
            None.
        """
        raw = '{"outer":{"inner":"value"}}'
        result = format_json(raw)

        assert '"outer"' in result
        assert '"inner": "value"' in result

    def test_format_json_invalid_raises(self) -> None:
        """Verify invalid JSON raises an error.

        Args:
            None.

        Returns:
            None.
        """
        with pytest.raises((json.JSONDecodeError, ValueError, SyntaxError)):
            format_json("not valid json")

    def test_format_json_single_quotes_fallback(self) -> None:
        """Verify single-quoted Python dicts are handled via fallback.

        Args:
            None.

        Returns:
            None.
        """
        raw = "{'key': 'value', 'num': 42}"
        result = format_json(raw)

        assert '"key": "value"' in result
        assert '"num": 42' in result


class TestOllamaClient:
    """Tests for Ollama client logic with mocked HTTP."""

    @patch("ollama_client.urllib.request.urlopen")
    def test_send_request_builds_correct_payload(self, mock_urlopen: MagicMock) -> None:
        """Verify the request payload is correctly structured.

        Args:
            mock_urlopen: Mock for `urllib.request.urlopen`.

        Returns:
            None.
        """
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"message": {"content": '{"result": "ok"}'}}
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        messages = [{"role": "user", "content": "test query"}]
        send_request("http://localhost:11434", "test-model", "system", messages)

        call_args = mock_urlopen.call_args[0][0]
        payload = json.loads(call_args.data.decode("utf-8"))

        assert payload["model"] == "test-model"
        assert payload["stream"] is False
        assert payload["format"] == "json"
        assert payload["keep_alive"] == -1
        assert payload["options"]["temperature"] == 0.1
        assert payload["messages"][0] == {"role": "system", "content": "system"}
        assert payload["messages"][1] == {"role": "user", "content": "test query"}

    @patch("ollama_client.urllib.request.urlopen")
    def test_send_request_returns_parsed_response(
        self, mock_urlopen: MagicMock
    ) -> None:
        """Verify the response is parsed as JSON.

        Args:
            mock_urlopen: Mock for `urllib.request.urlopen`.

        Returns:
            None.
        """
        expected = {"message": {"content": '{"workflow_id": "wf-1"}'}}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(expected).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = send_request("http://localhost:11434", "model", "sys", [])

        assert result == expected

    def test_extract_content_gets_message(self) -> None:
        """Verify content extraction from response dict.

        Args:
            None.

        Returns:
            None.
        """
        response = {"message": {"content": '{"key": "value"}'}}
        assert extract_content(response) == '{"key": "value"}'

    def test_extract_content_strips_whitespace(self) -> None:
        """Verify content extraction strips leading/trailing whitespace.

        Args:
            None.

        Returns:
            None.
        """
        response = {"message": {"content": '  {"key": "value"}  \n'}}
        assert extract_content(response) == '{"key": "value"}'

    @patch("ollama_client.urllib.request.urlopen")
    def test_send_request_uses_correct_url(self, mock_urlopen: MagicMock) -> None:
        """Verify the request URL includes the chat endpoint.

        Args:
            mock_urlopen: Mock for `urllib.request.urlopen`.

        Returns:
            None.
        """
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"message": {"content": "{}"}}
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        send_request("http://myhost:1234", "model", "sys", [])

        call_args = mock_urlopen.call_args[0][0]
        assert call_args.full_url == "http://myhost:1234/api/chat"

    @patch("ollama_client.urllib.request.urlopen")
    def test_send_request_passes_timeout(self, mock_urlopen: MagicMock) -> None:
        """Verify the timeout parameter is forwarded to `urlopen`.

        Args:
            mock_urlopen: Mock for `urllib.request.urlopen`.

        Returns:
            None.
        """
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"message": {"content": "{}"}}
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        send_request("http://localhost:11434", "model", "sys", [], timeout=60)

        assert mock_urlopen.call_args[1]["timeout"] == 60

    @patch("ollama_client.urllib.request.urlopen")
    def test_send_request_passes_custom_temperature(
        self, mock_urlopen: MagicMock
    ) -> None:
        """Verify a custom `temperature` is included in the payload options.

        Args:
            mock_urlopen: Mock for `urllib.request.urlopen`.

        Returns:
            None.
        """
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"message": {"content": "{}"}}
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        send_request("http://localhost:11434", "model", "sys", [], temperature=0.5)

        call_args = mock_urlopen.call_args[0][0]
        payload = json.loads(call_args.data.decode("utf-8"))
        assert payload["options"]["temperature"] == 0.5

    @patch("ollama_client.urllib.request.urlopen")
    def test_send_request_passes_custom_keep_alive(
        self, mock_urlopen: MagicMock
    ) -> None:
        """Verify custom keep_alive value is included in payload.

        Args:
            mock_urlopen: Mock for `urllib.request.urlopen`.

        Returns:
            None.
        """
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"message": {"content": "{}"}}
        ).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        send_request("http://localhost:11434", "model", "sys", [], keep_alive=600)

        call_args = mock_urlopen.call_args[0][0]
        payload = json.loads(call_args.data.decode("utf-8"))
        assert payload["keep_alive"] == 600

    @patch("ollama_client.urllib.request.urlopen")
    def test_preload_model_sends_correct_payload(self, mock_urlopen: MagicMock) -> None:
        """Verify `preload_model` sends the expected payload to Ollama.

        Args:
            mock_urlopen: Mock for `urllib.request.urlopen`.

        Returns:
            None.
        """
        mock_response = MagicMock()
        mock_response.read.return_value = b""
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        preload_model("http://localhost:11434", "test-model")

        call_args = mock_urlopen.call_args[0][0]
        payload = json.loads(call_args.data.decode("utf-8"))

        assert call_args.full_url == "http://localhost:11434/api/chat"
        assert payload["model"] == "test-model"
        assert payload["messages"] == []
        assert payload["keep_alive"] == -1

    @patch("ollama_client.urllib.request.urlopen")
    def test_preload_model_passes_timeout(self, mock_urlopen: MagicMock) -> None:
        """Verify `preload_model` forwards the `timeout` parameter to `urlopen`.

        Args:
            mock_urlopen: Mock for `urllib.request.urlopen`.

        Returns:
            None.
        """
        mock_response = MagicMock()
        mock_response.read.return_value = b""
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        preload_model("http://localhost:11434", "model", timeout=30)

        assert mock_urlopen.call_args[1]["timeout"] == 30
