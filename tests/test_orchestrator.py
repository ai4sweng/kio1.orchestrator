import json
from formatter import format_json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anthropic_client import create_client as create_anthropic_client
from anthropic_client import extract_content as extract_anthropic_content
from anthropic_client import preload as preload_anthropic
from anthropic_client import send_request as send_anthropic_request
from chat_history import (
    append_assistant_message,
    append_user_message,
    create_chat_file,
    load_messages,
)
from config_loader import Config, load_config
from ollama_client import extract_content, preload, send_request
from openai_client import create_client as create_openai_client
from openai_client import extract_content as extract_openai_content
from openai_client import preload as preload_openai
from openai_client import send_request as send_openai_request
from prompt_loader import load_prompt
from provider_client import load_provider


def make_config(
    provider: str = "ollama",
    allowed_providers: frozenset[str] = frozenset({"ollama", "openai", "anthropic"}),
    model: str = "test-model",
    temperature: float = 0.1,
    request_timeout: float = 120,
    keep_alive: int = -1,
    max_tokens: int = 1024,
    provider_options: dict[str, Any] | None = None,
) -> Config:
    """Create a configuration for provider tests.

    Args:
        provider: Provider name to configure.
        allowed_providers: Set of provider names permitted to load.
        model: Model identifier.
        temperature: Sampling temperature.
        request_timeout: Request timeout in seconds.
        keep_alive: Ollama keep_alive value in seconds, or -1 to keep loaded.
        max_tokens: Maximum number of generated output tokens.
        provider_options: Optional provider-specific configuration; defaults
            to an Ollama endpoint when not provided.

    Returns:
        A `Config` instance for use in tests.
    """
    if provider_options is None:
        provider_options = {"endpoint": "http://localhost:11434"}

    return Config(
        provider=provider,
        allowed_providers=allowed_providers,
        model=model,
        prompt_path="prompt.txt",
        chat_directory="chats",
        temperature=temperature,
        request_timeout=request_timeout,
        keep_alive=keep_alive,
        max_tokens=max_tokens,
        provider_options=provider_options,
    )


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
            "provider": "ollama",
            "allowed_providers": ["ollama", "openai", "anthropic"],
            "model": "test-model",
            "prompt_path": "prompts/test.txt",
            "chat_directory": "chats",
            "temperature": 0.1,
            "request_timeout": 120,
            "max_tokens": 2048,
            "provider_options": {
                "custom_option": "custom-value",
            },
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        config = load_config(str(config_file))

        assert config.provider == "ollama"
        assert config.allowed_providers == frozenset({"ollama", "openai", "anthropic"})
        assert config.model == "test-model"
        assert config.prompt_path == "prompts/test.txt"
        assert config.chat_directory == "chats"
        assert config.temperature == 0.1
        assert config.request_timeout == 120
        assert config.keep_alive == -1
        assert config.max_tokens == 2048
        assert config.provider_options == {"custom_option": "custom-value"}

    def test_load_config_rejects_unknown_provider(self, tmp_path: Path) -> None:
        """Verify an unknown provider value raises a clear error
        
        Args:
            tmp_path: Pytest temporary directory fixture.
        
        Returns:
            None.
        """
        config_data = {
            "provider": "unknown_provider",
            "allowed_providers": ["ollama", "openai", "anthropic"],
            "model": "test-model",
            "prompt_path": "prompts/test.txt",
            "chat_directory": "chats",
            "temperature": 0.1,
            "keep_alive": -1,
            "request_timeout": 120,
            "max_tokens": 2048,
            "provider_options": {
                "endpoint": "http://localhost:11434",
            },
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with pytest.raises(ValueError, match="Unknown provider"):
            load_config(str(config_file))

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
            "provider": "ollama",
            "allowed_providers": ["ollama", "openai", "anthropic"],
            "model": "test-model",
            "prompt_path": "prompts/test.txt",
            "chat_directory": "chats",
            "temperature": 0.1,
            "request_timeout": 120,
            "keep_alive": 600,
            "max_tokens": 2048,
            "provider_options": {
                "endpoint": "http://localhost:11434",
            },
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
            "provider": "ollama",
            "allowed_providers": ["ollama", "openai", "anthropic"],
            "model": "test-model",
            "prompt_path": "prompts/test.txt",
            "chat_directory": "chats",
            "temperature": 0.1,
            "request_timeout": 120,
            "keep_alive": invalid_keep_alive,
            "provider_options": {
                "endpoint": "http://localhost:11434",
            },
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
            provider="test_provider",
            allowed_providers=frozenset({"test_provider"}),
            model="m",
            prompt_path="p.txt",
            chat_directory="c",
            temperature=0.1,
            request_timeout=120,
            keep_alive=-1,
            max_tokens=2048,
            provider_options={"example": True},
        )
        assert config.provider == "test_provider"
        assert config.model == "m"
        assert config.temperature == 0.1
        assert config.request_timeout == 120
        assert config.keep_alive == -1
        assert config.max_tokens == 2048
        assert config.provider_options == {"example": True}


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

    def test_format_json_removes_markdown_fence(self) -> None:
        """Verify fenced JSON is normalized before parsing.

        Args:
            None.

        Returns:
            None.
        """
        raw = """```json
        {"key": "value", "num": 42}
        ```"""

        result = format_json(raw)

        expected = '{\n  "key": "value",\n  "num": 42\n}'
        assert result == expected


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
        config = make_config(model="test-model")
        send_request(config, None, "system", messages)

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

        config = make_config(model="model")
        result = send_request(config, None, "sys", [])

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

        config = make_config(
            model="model", provider_options={"endpoint": "http://myhost:1234"}
        )
        send_request(config, None, "sys", [])

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

        config = make_config(model="model", request_timeout=60)
        send_request(config, None, "sys", [])

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

        config = make_config(model="model", temperature=0.5)
        send_request(config, None, "sys", [])

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

        config = make_config(model="model", keep_alive=600)
        send_request(config, None, "sys", [])

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

        config = make_config(model="test-model")
        preload(config, None)

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

        config = make_config(model="model", request_timeout=30)
        preload(config, None)

        assert mock_urlopen.call_args[1]["timeout"] == 30


class TestOpenAIClient:
    """Tests for OpenAI client logic with mocked SDK calls."""

    @patch("openai_client.OpenAI")
    def test_create_client_uses_api_key_and_timeout(
        self, mock_openai: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify OpenAI client is created with timeout.

        Args:
            mock_openai: Mock for `openai_client.OpenAI`.
            monkeypatch: Pytest fixture for modifying environment variables.

        Returns:
            None.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
        config = make_config(
            provider="openai", request_timeout=12.0, provider_options={}
        )
        create_openai_client(config)

        mock_openai.assert_called_once_with(api_key="test-api-key", timeout=12.0)

    @patch("openai_client.OpenAI")
    def test_create_client_rejects_missing_api_key(
        self,
        mock_openai: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify a clear error is raised when the API key is missing.

        Args:
            mock_openai: Mock for `openai_client.OpenAI`.
            monkeypatch: Pytest fixture for modifying environment variables.

        Returns:
            None.
        """
        monkeypatch.delenv(
            "OPENAI_API_KEY",
            raising=False,
        )
        config = make_config(
            provider="openai",
            provider_options={},
        )

        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            create_openai_client(config)

        mock_openai.assert_not_called()

    @patch("openai_client.OpenAI")
    def test_create_client_rejects_blank_api_key(
        self,
        mock_openai: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify a whitespace-only API key is rejected.

        Args:
            mock_openai: Mock for `openai_client.OpenAI`.
            monkeypatch: Pytest fixture for modifying environment variables.

        Returns:
            None.
        """
        monkeypatch.setenv(
            "OPENAI_API_KEY",
            "   ",
        )
        config = make_config(
            provider="openai",
            provider_options={},
        )

        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            create_openai_client(config)

        mock_openai.assert_not_called()

    def test_send_request_calls_chat_completions_create(self) -> None:
        """Verify OpenAI Chat Completions API call is correctly structured.

        Args:
            None.

        Returns:
            None.
        """
        client = MagicMock()
        messages = [{"role": "user", "content": "Build an app"}]

        config = make_config(
            provider="openai", temperature=0.2, max_tokens=500, provider_options={}
        )
        send_openai_request(
            config,
            client,
            "system prompt",
            messages,
        )

        client.chat.completions.create.assert_called_once_with(
            model="test-model",
            messages=[{"role": "system", "content": "system prompt"}, *messages],
            temperature=0.2,
            response_format={
                "type": "json_object",
            },
        )

    def test_extract_openai_content_gets_message_content(self) -> None:
        """Verify content extraction from OpenAI response object.

        Args:
            None.

        Returns:
            None.
        """
        response = MagicMock()
        response.choices[0].message.content = '{"result": "ok"}'

        assert extract_openai_content(response) == '{"result": "ok"}'

    def test_extract_openai_content_strips_whitespace(self) -> None:
        """Verify content extraction strips leading/trailing whitespace.

        Args:
            None.

        Returns:
            None.
        """
        response = MagicMock()
        response.choices[0].message.content = '  {"result": "ok"}  \n'

        assert extract_openai_content(response) == '{"result": "ok"}'

    def test_preload_verifies_model_exists(self) -> None:
        """Verify preload retrieves the model to validate it exists.

        Args:
            None.

        Returns:
            None.
        """
        client = MagicMock()
        config = make_config(provider="openai", model="gpt-4o-mini")

        preload_openai(config, client)

        client.models.retrieve.assert_called_once_with("gpt-4o-mini")


class TestAnthropicClient:
    """Tests for Anthropic client logic with mocked SDK calls."""

    @patch("anthropic_client.Anthropic")
    def test_create_client_uses_api_key_and_timeout(
        self, mock_anthropic: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify Anthropic client is created with API key and timeout.

        Args:
            mock_anthropic: Mock for `anthropic_client.Anthropic`.
            monkeypatch: Pytest monkeypatch fixture to set environment variables.

        Returns:
            None.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
        config = make_config(
            provider="anthropic", request_timeout=12.0, provider_options={}
        )
        create_anthropic_client(config)

        mock_anthropic.assert_called_once_with(api_key="test-api-key", timeout=12.0)

    @patch("anthropic_client.Anthropic")
    def test_create_client_rejects_missing_api_key(
        self,
        mock_anthropic: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify a clear error is raised when the API key is missing.

        Args:
            mock_anthropic: Mock for `anthropic_client.Anthropic`.
            monkeypatch: Pytest fixture for modifying environment variables.

        Returns:
            None.
        """
        monkeypatch.delenv(
            "ANTHROPIC_API_KEY",
            raising=False,
        )
        config = make_config(
            provider="anthropic",
            provider_options={},
        )

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            create_anthropic_client(config)

        mock_anthropic.assert_not_called()

    @patch("anthropic_client.Anthropic")
    def test_create_client_rejects_blank_api_key(
        self,
        mock_anthropic: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify a whitespace-only API key is rejected.

        Args:
            mock_anthropic: Mock for `anthropic_client.Anthropic`.
            monkeypatch: Pytest fixture for modifying environment variables.

        Returns:
            None.
        """
        monkeypatch.setenv(
            "ANTHROPIC_API_KEY",
            "   ",
        )
        config = make_config(
            provider="anthropic",
            provider_options={},
        )

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            create_anthropic_client(config)

        mock_anthropic.assert_not_called()

    def test_send_request_calls_messages_create(self) -> None:
        """Verify Anthropic Messages API call is correctly structured.

        Args:
            None.

        Returns:
            None.
        """
        client = MagicMock()
        messages = [{"role": "user", "content": "Build an app"}]

        config = make_config(
            provider="anthropic", temperature=0.2, max_tokens=500, provider_options={}
        )
        send_anthropic_request(
            config=config,
            client=client,
            system_prompt="system prompt",
            messages=messages,
        )

        client.messages.create.assert_called_once_with(
            model="test-model",
            system="system prompt",
            messages=messages,
            temperature=0.2,
            max_tokens=500,
        )

    def test_send_request_omits_temperature_for_unsupported_model(self) -> None:
        """Verify temperature is omitted for models that reject a custom value.

        Args:
            None.

        Returns:
            None.
        """
        client = MagicMock()
        messages = [{"role": "user", "content": "Build an app"}]

        config = make_config(provider="anthropic", model="claude-opus-4-8")
        send_anthropic_request(config, client, "system prompt", messages)

        client.messages.create.assert_called_once_with(
            model="claude-opus-4-8",
            system="system prompt",
            messages=messages,
            max_tokens=1024,
        )

    def test_extract_anthropic_content_gets_text_blocks(self) -> None:
        """Verify content extraction from Anthropic text blocks.

        Args:
            None.

        Returns:
            None.
        """
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = '{"result": "ok"}'
        response = MagicMock()
        response.content = [text_block]

        assert extract_anthropic_content(response) == '{"result": "ok"}'

    def test_extract_anthropic_content_strips_whitespace(self) -> None:
        """Verify content extraction strips leading/trailing whitespace.

        Args:
            None.

        Returns:
            None.
        """
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = '  {"result": "ok"}  \n'
        response = MagicMock()
        response.content = [text_block]

        assert extract_anthropic_content(response) == '{"result": "ok"}'

    def test_preload_verifies_model_exists(self) -> None:
        """Verify preload retrieves the model to validate it exists.

        Args:
            None.

        Returns:
            None.
        """
        client = MagicMock()
        config = make_config(provider="anthropic", model="claude-haiku-4-5")

        preload_anthropic(config, client)

        client.models.retrieve.assert_called_once_with("claude-haiku-4-5")


class TestProviderLoading:
    """Tests for dynamic provider discovery."""

    @pytest.mark.parametrize(
        "provider_name",
        [
            "ollama",
            "openai",
            "anthropic",
        ],
    )
    def test_load_provider(self, provider_name: str) -> None:
        """Verify configured provider modules load successfully.

        Args:
            provider_name: Name of the provider module to load.

        Returns:
            None.
        """
        provider = load_provider(
            provider_name, frozenset({"ollama", "openai", "anthropic"})
        )

        assert callable(provider.create_client)
        assert callable(provider.preload)
        assert callable(provider.send_request)
        assert callable(provider.extract_content)

    def test_load_provider_rejects_invalid_name(self) -> None:
        """Verify unsafe module names are rejected.

        Args:
            None.

        Returns:
            None.
        """
        with pytest.raises(ValueError, match="Invalid provider name"):
            load_provider("../openai", frozenset({"ollama", "openai", "anthropic"}))

    def test_load_provider_rejects_unknown_provider(self) -> None:
        """Verify a provider outside the allowlist produces a useful error.

        Args:
            None.

        Returns:
            None.
        """
        with pytest.raises(ValueError, match="Invalid provider name"):
            load_provider(
                "missing_provider", frozenset({"ollama", "openai", "anthropic"})
            )

    def test_load_provider_respects_restricted_allowlist(self) -> None:
        """Verify a provider excluded from the caller's allowlist is rejected.

        Args:
            None.

        Returns:
            None.
        """
        with pytest.raises(ValueError, match="Invalid provider name"):
            load_provider("openai", frozenset({"ollama"}))
