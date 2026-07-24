import json
import urllib.request
from typing import Any

from config_loader import Config


def create_client(config: Config) -> None:
    """Return None because Ollama uses direct HTTP requests."""
    return None


def preload(config: Config, client: Any) -> None:
    """Preload a model into Ollama's memory.

    The context window is sent here as well as on requests, because Ollama
    allocates its cache when the model loads. Loading with a smaller window
    than requests use would force a reload on the first request.

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
        "options": {
            "num_ctx": _get_num_ctx(config),
        },
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

    Raises:
        ValueError: If the response was truncated by the context window.
    """
    endpoint = _get_endpoint(config)
    num_ctx = _get_num_ctx(config)
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
            "num_ctx": num_ctx,
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

    if response_data.get("done_reason") == "length":
        raise ValueError(
            f"Response truncated before completion: "
            f"prompt={response_data.get('prompt_eval_count')} tokens, "
            f"output={response_data.get('eval_count')} tokens, "
            f"num_ctx={num_ctx}. Increase provider_options.num_ctx "
            f"or start a new session."
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


def _get_num_ctx(config: Config) -> int:
    """Read and validate the Ollama context window size.

    Args:
        config: Application configuration containing provider options.

    Returns:
        The configured context window size in tokens.

    Raises:
        ValueError: If provider_options.num_ctx is missing or not a positive
            integer, or if max_tokens leaves no room for the prompt. Ollama
            clamps zero and negative values to a roughly 4-token window and
            returns HTTP 200 rather than rejecting them, so invalid values
            must be caught here.
    """
    num_ctx = config.provider_options.get("num_ctx")

    if type(num_ctx) is not int or num_ctx <= 0:
        raise ValueError(
            "Ollama requires provider_options.num_ctx as a positive integer"
        )

    if config.max_tokens >= num_ctx:
        raise ValueError(
            f"max_tokens ({config.max_tokens}) must be smaller than "
            f"provider_options.num_ctx ({num_ctx}) to leave room for the prompt"
        )

    return num_ctx
