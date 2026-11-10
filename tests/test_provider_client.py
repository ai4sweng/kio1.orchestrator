"""Hosted-provider configuration, transport, privacy, and integration checks."""

import json
import logging
import urllib.error
import urllib.request
from http.client import HTTPMessage
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ai import provider_client
from chat_history import create_chat_file, load_messages
from components.chat_service import run_turn
from config_loader import Config, load_config


@pytest.fixture(params=["groq", "openrouter"])
def config(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Return hosted settings with a synthetic environment credential.

    Args:
        request: Provider parametrization.
        monkeypatch: Isolated credential environment.

    Returns:
        Hosted provider configuration.
    """
    key_env = "GROQ_API_KEY" if request.param == "groq" else "OPENROUTER_API_KEY"
    monkeypatch.setenv(key_env, "synthetic-private-api-key")
    return Config(
        model="test-model",
        prompt_path="prompt.txt",
        chat_directory="chats",
        temperature=0.2,
        request_timeout=5,
        keep_alive=-1,
        max_output_tokens=1024,
        endpoint="http://localhost:11434",
        context_window_size=16384,
        provider=request.param,
        api_key_env=key_env,
    )


@pytest.fixture
def completion() -> dict[str, Any]:
    """Return a realistic completion without contacting a paid service.

    Returns:
        OpenAI-compatible JSON completion response.
    """
    return {
        "id": "response-123",
        "model": "test-model",
        "choices": [
            {"finish_reason": "stop", "message": {"content": ' {"ok": true} '}}
        ],
        "usage": {"prompt_tokens": 30, "completion_tokens": 8},
    }


def mock_response(opener: MagicMock, body: Any) -> MagicMock:
    """Set an opener mock's HTTP response body.

    Args:
        opener: Mocked urllib opener factory.
        body: JSON-serializable response or raw response bytes.

    Returns:
        The response mock for status and size assertions.
    """
    response = opener.return_value.open.return_value.__enter__.return_value
    response.status = 200
    response.read.return_value = (
        body if isinstance(body, bytes) else json.dumps(body).encode()
    )
    return response


def test_hosted_configuration(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Load provider defaults without requiring Ollama-only fields.

    Args:
        config: Hosted settings.
        tmp_path: Configuration directory.
        monkeypatch: Environment override isolation.

    Returns:
        None; asserts provider and credential-reference defaults.
    """
    data = {
        "provider": config.provider,
        "model": config.model,
        "prompt_path": config.prompt_path,
        "chat_directory": config.chat_directory,
        "temperature": config.temperature,
        "request_timeout": config.request_timeout,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("KIO1_OLLAMA_ENDPOINT", "http://wrong-ollama:11434")
    loaded = load_config(str(path))
    assert loaded.provider == config.provider
    assert loaded.api_key_env == config.api_key_env
    assert (
        loaded.endpoint + "/chat/completions"
        == provider_client.HOSTED_ENDPOINTS[config.provider]
    )
    assert "synthetic-private-api-key" not in repr(loaded)
    monkeypatch.delenv(config.api_key_env)
    with pytest.raises(ValueError, match=config.api_key_env):
        load_config(str(path))
    data["api_key_env"] = "CUSTOM_LLM_KEY"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("CUSTOM_LLM_KEY", "custom-synthetic-key")
    assert load_config(str(path)).api_key_env == "CUSTOM_LLM_KEY"
    data["provider"] = "unsupported"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="provider"):
        load_config(str(path))


@pytest.mark.parametrize(
    "configured_name",
    [
        "gsk_synthetic_private_key",
        "sk-or-v1-synthetic-private-key",
        "invalid variable name",
        "invalid\x00name",
        "UNSET_CUSTOM_CREDENTIAL_NAME",
    ],
)
def test_credential_reference_errors_do_not_expose_values(
    config: Config,
    configured_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep mistyped keys out of both startup and request error messages.

    Args:
        config: Hosted settings with a valid default credential.
        configured_name: Mistyped key or missing credential reference.
        tmp_path: Scratch configuration directory.
        monkeypatch: Isolated environment variables.
        caplog: Captured application diagnostics.

    Returns:
        None; configuration and transport errors never reflect the field value.
    """
    if configured_name.startswith("gsk_"):
        monkeypatch.setenv(configured_name, "synthetic-key-value")
    if configured_name == "UNSET_CUSTOM_CREDENTIAL_NAME":
        monkeypatch.delenv(configured_name, raising=False)
    config.api_key_env = configured_name
    data = vars(config).copy()
    data.pop("telemetry")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError) as startup_error:
        load_config(str(path))
    with patch("ai.provider_client.urllib.request.build_opener") as opener:
        with pytest.raises(ValueError) as request_error:
            provider_client.send_request(config, "system", [])
        opener.assert_not_called()
    for error in (startup_error.value, request_error.value):
        assert "api_key_env" in str(error)
        assert configured_name not in str(error)
    assert configured_name not in caplog.text


@pytest.mark.parametrize("environment_present", [False, True])
def test_direct_config_key(
    config: Config,
    completion: dict[str, Any],
    environment_present: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Use a key pasted in config without requiring or preferring an export.

    Args:
        config: Hosted settings.
        completion: Mock provider response.
        environment_present: Whether a competing environment credential exists.
        tmp_path: Scratch configuration directory.
        monkeypatch: Environment isolation.
        caplog: Captured application logs.

    Returns:
        None; direct credentials win and remain absent from representations/logs.
    """
    if not environment_present:
        monkeypatch.delenv(config.api_key_env)
    config.api_key = "  gsk_synthetic_direct_private_key  "
    config.api_key_env = "gsk_ignored_private_reference"
    data = vars(config).copy()
    data.pop("telemetry")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with caplog.at_level(logging.DEBUG), patch(
        "ai.provider_client.urllib.request.build_opener"
    ) as opener:
        loaded = load_config(str(path))
        mock_response(opener, completion)
        provider_client.send_request(loaded, "system", [])
    assert loaded.api_key == config.api_key.strip()
    request = opener.return_value.open.call_args.args[0]
    assert (
        request.get_header("Authorization") == "Bearer gsk_synthetic_direct_private_key"
    )
    for private in (loaded.api_key, config.api_key_env):
        assert private not in repr(loaded)
        assert private not in caplog.text


@pytest.mark.parametrize(
    "invalid_key",
    [
        None,
        123,
        True,
        [],
        "gsk_fake\nheader",
        "gsk_fake\x00key",
        "gsk_fake\x7fkey",
        "gsk_fake\u00e9key",
    ],
)
def test_invalid_direct_config_key(
    config: Config, invalid_key: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Reject invalid direct keys without exposing them or making HTTP requests.

    Args:
        config: Hosted settings.
        invalid_key: Invalid direct credential type or header value.
        tmp_path: Scratch configuration directory.
        caplog: Captured application logs.

    Returns:
        None; both startup and request validation reject unsafe credentials.
    """
    config.api_key = invalid_key
    data = vars(config).copy()
    data.pop("telemetry")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError) as startup_error:
        load_config(str(path))
    with patch("ai.provider_client.urllib.request.build_opener") as opener:
        with pytest.raises(ValueError) as request_error:
            provider_client.send_request(config, "system", [])
        opener.assert_not_called()
    if isinstance(invalid_key, str):
        assert (
            invalid_key
            not in str(startup_error.value) + str(request_error.value) + caplog.text
        )


def test_hosted_request_and_logs(
    config: Config, completion: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Check auth, schema, JSON mode, token telemetry, and log privacy.

    Args:
        config: Hosted settings.
        completion: Provider response.
        caplog: Captured diagnostic logs.

    Returns:
        None; verifies metadata without prompts, replies, or credentials.
    """
    with patch("ai.provider_client.urllib.request.build_opener") as opener, patch(
        "ai.provider_client.record_gen_ai_response"
    ) as record, caplog.at_level(logging.DEBUG, logger="main"):
        response = mock_response(opener, completion)
        result = provider_client.send_request(
            config, "private-system", [{"role": "user", "content": "private-query"}]
        )
    request = opener.return_value.open.call_args.args[0]
    payload = json.loads(request.data)
    assert request.full_url == provider_client.HOSTED_ENDPOINTS[config.provider]
    assert request.get_header("User-agent") == "KIO1-Orchestrator/1.0"
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("Authorization") == "Bearer synthetic-private-api-key"
    assert payload == {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "private-system"},
            {"role": "user", "content": "private-query"},
        ],
        "temperature": 0.2,
        "max_tokens": 1024,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    assert opener.return_value.open.call_args.kwargs["timeout"] == 5
    response.read.assert_called_once_with(provider_client.MAX_RESPONSE_BYTES + 1)
    assert isinstance(opener.call_args.args[0], urllib.request.HTTPRedirectHandler)
    assert (
        opener.call_args.args[0].redirect_request(
            request, BytesIO(), 302, "Found", HTTPMessage(), "https://untrusted.invalid"
        )
        is None
    )
    assert provider_client.extract_content(result) == '{"ok": true}'
    assert record.call_args.kwargs["provider"] == config.provider
    assert record.call_args.kwargs["input_tokens"] == 30
    assert record.call_args.kwargs["output_tokens"] == 8
    assert record.call_args.kwargs["response_id"] == "response-123"
    assert f"provider={config.provider}" in caplog.text
    assert "duration_ms=" in caplog.text and "http_status=200" in caplog.text
    for private in (
        "synthetic-private-api-key",
        "private-system",
        "private-query",
        '{"ok": true}',
    ):
        assert private not in caplog.text


@pytest.mark.parametrize("status", [302, 401, 402, 403, 429, 500, 503])
def test_http_errors_are_safe(
    config: Config, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Reject redirects and HTTP failures without exposing remote bodies.

    Args:
        config: Hosted settings.
        status: HTTP failure code.
        caplog: Diagnostic capture.

    Returns:
        None; verifies sanitized failure and no automatic retry.
    """
    error = urllib.error.HTTPError(
        "https://example.invalid",
        status,
        "private-error",
        HTTPMessage(),
        BytesIO(b"private-body"),
    )
    with patch("ai.provider_client.urllib.request.build_opener") as opener:
        opener.return_value.open.side_effect = error
        with pytest.raises(RuntimeError, match=f"HTTP {status}") as raised:
            provider_client.send_request(config, "system", [])
    opener.return_value.open.assert_called_once()
    assert f"http_status={status}" in caplog.text
    assert "phase=http_status" in caplog.text
    assert "phase=http_status" in str(raised.value)
    if config.provider == "openrouter" and status == 402:
        assert "insufficient credits" in str(raised.value)
        assert "API-key spending limit" in str(raised.value)
        assert "reduce max_output_tokens" in str(raised.value)
        assert f"{config.max_output_tokens} tokens" in str(raised.value)
    else:
        assert "OpenRouter reports" not in str(raised.value)
    for private in ("private-error", "private-body", "synthetic-private-api-key"):
        assert private not in caplog.text + str(raised.value)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("private-timeout"),
        urllib.error.URLError("private-network"),
        ValueError("private-header"),
    ],
)
def test_network_errors_are_safe(config: Config, error: Exception) -> None:
    """Sanitize connection and timeout failures.

    Args:
        config: Hosted settings.
        error: Simulated transport failure.

    Returns:
        None; verifies a safe, actionable error.
    """
    with patch("ai.provider_client.urllib.request.build_opener") as opener:
        opener.return_value.open.side_effect = error
        with pytest.raises(RuntimeError, match="connectivity") as raised:
            provider_client.send_request(config, "system", [])
    assert "private-" not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        [],
        {"error": "private-error"},
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
        {"choices": [{"finish_reason": "content_filter"}]},
        b"x" * (provider_client.MAX_RESPONSE_BYTES + 1),
    ],
    ids=[
        "invalid-json",
        "array",
        "error-envelope",
        "missing-choices",
        "empty-choices",
        "bad-choice",
        "empty-content",
        "filtered",
        "oversized",
    ],
)
def test_invalid_responses(config: Config, body: Any) -> None:
    """Fail closed on malformed, filtered, or oversized provider output.

    Args:
        config: Hosted settings.
        body: Invalid provider response.

    Returns:
        None; invalid output never becomes an assistant message.
    """
    with patch("ai.provider_client.urllib.request.build_opener") as opener:
        mock_response(opener, body)
        with pytest.raises(ValueError):
            provider_client.send_request(config, "system", [])


def test_truncation_and_bad_metadata(
    config: Config, completion: dict[str, Any]
) -> None:
    """Record truncation and tolerate missing or invalid usage metadata.

    Args:
        config: Hosted settings.
        completion: Mutable provider response.

    Returns:
        None; checks truncation telemetry and sanitized token counts.
    """
    with patch("ai.provider_client.urllib.request.build_opener") as opener, patch(
        "ai.provider_client.record_gen_ai_response"
    ) as record:
        completion["choices"][0]["finish_reason"] = "length"
        mock_response(opener, completion)
        with pytest.raises(ValueError, match="truncated"):
            provider_client.send_request(config, "system", [])
        assert record.call_args.kwargs["truncated"] is True
        completion["choices"][0]["finish_reason"] = "stop"
        completion.update(
            usage={"prompt_tokens": "private-invalid-count", "completion_tokens": -1},
            model={},
            id=123,
        )
        mock_response(opener, completion)
        provider_client.send_request(config, "system", [])
        assert record.call_args.kwargs["input_tokens"] is None
        assert record.call_args.kwargs["output_tokens"] is None
        assert record.call_args.kwargs["response_model"] is None
        assert record.call_args.kwargs["response_id"] is None


def test_preload_and_missing_credentials(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skip hosted preloading and reject credentials before network access.

    Args:
        config: Hosted settings.
        monkeypatch: Credential isolation.

    Returns:
        None; verifies no request on preload or missing credentials.
    """
    with patch("ai.provider_client.urllib.request.build_opener") as opener:
        provider_client.preload_model(config)
        monkeypatch.delenv(config.api_key_env)
        with pytest.raises(ValueError, match=config.api_key_env):
            provider_client.send_request(config, "system", [])
        opener.assert_not_called()


def test_turn_persistence(
    config: Config, completion: dict[str, Any], tmp_path: Path
) -> None:
    """Exercise provider selection through the real chat-turn service.

    Args:
        config: Hosted settings.
        completion: Hosted response.
        tmp_path: Isolated history storage.

    Returns:
        None; only successful replies enter persistent conversation history.
    """
    chat_file = create_chat_file(str(tmp_path), "system", "session")
    with patch("ai.provider_client.urllib.request.build_opener") as opener:
        mock_response(opener, completion)
        assert (
            run_turn(config, "system", chat_file, "session", 1, "query")
            == '{"ok": true}'
        )
        messages = load_messages(chat_file)
        assert messages[-2:] == [
            {"role": "user", "content": "query"},
            {"role": "assistant", "content": '{"ok": true}'},
        ]
        opener.return_value.open.side_effect = TimeoutError()
        with pytest.raises(RuntimeError):
            run_turn(config, "system", chat_file, "session", 2, "failed-query")
        assert load_messages(chat_file) == messages
