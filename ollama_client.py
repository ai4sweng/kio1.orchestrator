import json
import urllib.request
from typing import Any

from config_loader import Config

# provider_options keys used: "endpoint" (required) — the base URL of the Ollama server.


def create_client(config: Config) -> None:
    """Return None because Ollama uses direct HTTP requests."""
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
        "keep_alive": -1,
    }

    request_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=request_data,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=config.request_timeout) as response:
        response.read()


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
        "keep_alive": -1,
        "options": {
            "temperature": config.temperature,
            "num_predict": config.max_tokens,
        },
    }

    request_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=request_data,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=config.request_timeout) as response:
        response_data = json.loads(response.read().decode("utf-8"))

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
    """Read and validate the Ollama endpoint."""
    endpoint = config.provider_options.get("endpoint")

    if not isinstance(endpoint, str) or not endpoint:
        raise ValueError(
            "Ollama requires provider_options.endpoint"
        )

    return endpoint.rstrip("/")