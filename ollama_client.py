import json
import urllib.request
from typing import Any


def preload_model(endpoint: str, model: str, timeout: int = 120) -> None:
    """Preload a model into Ollama's memory.

    Args:
        endpoint: The Ollama base URL.
        model: The model name to preload.
        timeout: Request timeout in seconds.

    Returns:
        None.
    """
    url = f"{endpoint}/api/chat"
    payload = {
        "model": model,
        "messages": [],
        "keep_alive": -1,
    }

    request_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=request_data,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        response.read()


def send_request(
    endpoint: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    temperature: float = 0.1,
    timeout: int = 120,
) -> dict[str, Any]:
    """Send a chat completion request to the Ollama API.

    Args:
        endpoint: The Ollama base URL (e.g., `http://localhost:11434`).
        model: The model name to use (e.g., `ministral-3:8b`).
        system_prompt: The system prompt text.
        messages: List of message dicts with `role` and `content` keys.
        temperature: Sampling temperature for the model.
        timeout: Request timeout in seconds.

    Returns:
        The parsed JSON response from the model as a dictionary.
    """
    url = f"{endpoint}/api/chat"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            *messages,
        ],
        "stream": False,
        "format": "json",
        "keep_alive": -1,
        "options": {
            "temperature": temperature,
        },
    }

    request_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=request_data,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=timeout) as response:
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
