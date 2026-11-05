"""HTTP client for the local Ollama chat API."""

import json
import logging
import urllib.request
from typing import Any

from config_loader import Config
from session_logger import measure_duration
from telemetry import record_gen_ai_response

logger = logging.getLogger("main")
PROVIDER_NAME = "ollama"


def preload_model(config: Config) -> None:
    """Preload the configured model and context window into Ollama.

    Args:
        config: Application configuration.

    Returns:
        None.
    """
    url = f"{config.endpoint}/api/chat"
    payload = {
        "model": config.model,
        "messages": [],
        "keep_alive": config.keep_alive,
        "options": {"num_ctx": config.context_window_size},
    }

    request_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=request_data,
        headers={"Content-Type": "application/json"},
    )

    with measure_duration() as elapsed:
        with urllib.request.urlopen(req, timeout=config.request_timeout) as response:
            response.read()

    logger.info("Model preloaded: model=%s duration_ms=%d", config.model, elapsed())


def send_request(
    config: Config,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Send a chat completion request to the Ollama API.

    Args:
        config: Application configuration.
        system_prompt: The system prompt text.
        messages: List of message dicts with `role` and `content` keys.

    Returns:
        The parsed JSON response from the model as a dictionary.

    Raises:
        ValueError: If the response was truncated by the context window or output limit.
    """
    url = f"{config.endpoint}/api/chat"

    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
        ],
        "stream": False,
        "format": "json",
        "keep_alive": config.keep_alive,
        "options": {
            "temperature": config.temperature,
            "num_ctx": config.context_window_size,
            "num_predict": config.max_output_tokens,
        },
    }

    logger.debug("Request payload: %s", payload)

    request_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=request_data,
        headers={"Content-Type": "application/json"},
    )

    with measure_duration() as elapsed:
        with urllib.request.urlopen(req, timeout=config.request_timeout) as response:
            response_data = json.loads(response.read().decode("utf-8"))

    duration_ms = elapsed()
    finish_reason = response_data.get("done_reason")
    input_tokens = response_data.get("prompt_eval_count")
    output_tokens = response_data.get("eval_count")
    tokens_per_second = (
        output_tokens / (duration_ms / 1000)
        if isinstance(output_tokens, int) and duration_ms > 0
        else None
    )

    record_gen_ai_response(
        provider=PROVIDER_NAME,
        request_model=config.model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        response_model=response_data.get("model"),
        finish_reason=finish_reason,
        truncated=finish_reason == "length",
        truncation_reason="length",
    )

    logger.info(
        "Response received: model=%s duration_ms=%d input_tokens=%s "
        "output_tokens=%s tokens_per_second=%s",
        config.model,
        duration_ms,
        input_tokens,
        output_tokens,
        f"{tokens_per_second:.1f}" if tokens_per_second is not None else "n/a",
    )
    logger.debug("Response: %s", response_data)

    if finish_reason == "length":
        raise ValueError(
            f"Response truncated before completion: "
            f"prompt={response_data.get('prompt_eval_count')} tokens, "
            f"output={response_data.get('eval_count')} tokens, "
            f"context_window_size={config.context_window_size}, "
            f"max_output_tokens={config.max_output_tokens}. "
            "Increase max_output_tokens if the output limit was reached; "
            "otherwise increase context_window_size or start a new session."
        )

    return response_data


def extract_content(response: dict[str, Any]) -> str:
    """Extract the assistant message content from an Ollama response.

    Args:
        response: The full Ollama API response dictionary.

    Returns:
        The assistant's message content as a string.
    """
    return response["message"]["content"].strip()
