import json
import logging
import urllib.request
from typing import Any

from config_loader import Config
from session_logger import measure_duration

logger = logging.getLogger(__name__)


def create_client(config: Config) -> None:
    """Return None because Ollama uses direct HTTP requests."""
    logger.debug("Created ollama client (no-op, using direct HTTP requests)")
    return None


def preload(config: Config, client: Any) -> None:
    """Preload a model into Ollama's memory.

    Args:
        config: Application configuration.
        client: Not used for Ollama, included for interface consistency.

    Returns:
        None.
    """
    endpoint = _get_endpoint(config)
    url = f"{endpoint}/api/chat"
    payload = {
        "model": config.model,
        "messages": [],
        "keep_alive": config.keep_alive,
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
    client: Any,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Send a chat completion request to the Ollama API.

    Args:
        config: Application configuration.
        client: Not used for Ollama, included for interface consistency.
        system_prompt: The system prompt text.
        messages: List of message dicts with `role` and `content` keys.

    Returns:
        The parsed JSON response from the model as a dictionary.
    """
    endpoint = _get_endpoint(config)
    url = f"{endpoint}/api/chat"

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
        },
    }

    logger.debug(
        "Request payload: model=%s temperature=%s keep_alive=%s messages=%s",
        config.model,
        config.temperature,
        config.keep_alive,
        messages,
    )

    request_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=request_data,
        headers={"Content-Type": "application/json"},
    )

    with measure_duration() as elapsed:
        with urllib.request.urlopen(req, timeout=config.request_timeout) as response:
            response_data = json.loads(response.read().decode("utf-8"))

    logger.info(
        "Response received: model=%s duration_ms=%d input_tokens=%s output_tokens=%s",
        config.model,
        elapsed(),
        response_data.get("prompt_eval_count"),
        response_data.get("eval_count"),
    )
    logger.debug("Response: %s", response_data)

    return response_data


def extract_content(response: dict[str, Any]) -> str:
    """Extract the assistant message content from an Ollama response.

    Args:
        response: The full Ollama API response dictionary.

    Returns:
        The assistant's message content as a string.
    """
    return response["message"]["content"].strip()


def _get_endpoint(config: Config) -> str:
    """Read and validate the Ollama endpoint.

    Args:
        config: Application configuration containing provider options.

    Returns:
        The normalized Ollama endpoint without a trailing slash.

    Raises:
        ValueError: If provider_options.endpoint is missing or invalid.
    """
    endpoint = config.provider_options.get("endpoint")

    if not isinstance(endpoint, str) or not endpoint:
        raise ValueError("Ollama requires provider_options.endpoint")

    return endpoint.rstrip("/")
