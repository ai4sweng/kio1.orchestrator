import os

from typing import Any

from openai import OpenAI

from config_loader import Config


def create_client(config: Config) -> OpenAI:
    """Create an OpenAI client.

    Args:
        config: Application configuration.

    Returns:
        An OpenAI client.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError(
            "OPENAI_API_KEY environment variable is required when using the OpenAI provider"
        )
    return OpenAI(
        api_key=api_key,
        timeout=config.request_timeout,
    )


def preload(config: Config, client: Any) -> None:
    """Perform no preload operation for OpenAI."""
    return None


def send_request(
    config: Config,
    client: OpenAI,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> Any:
    """Send a request to the OpenAI Chat Completions API.

    Args:
        config: Application configuration.
        client: OpenAI client.
        system_prompt: System prompt text.
        messages: Conversation messages.

    Returns:
        An OpenAI response.
    """
    return client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": system_prompt},
            *messages
        ],
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        response_format={"type": "json_object"},
    )


def extract_content(response: Any) -> str:
    """Extract assistant text from an OpenAI response.

    Args:
        response: The response returned by the OpenAI Chat Completions API.

    Returns:
        The assistant message content with surrounding whitespace removed.
    """
    return response.choices[0].message.content.strip()
