import os
from typing import Any, cast

from anthropic import Anthropic
from anthropic.types import MessageParam

from config_loader import Config

# Models that reject a custom `temperature` (400 if sent with a non-default value).
# Omitting the parameter entirely is always safe — the API falls back to its own default.
_NO_CUSTOM_TEMPERATURE = {
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-mythos-5",
}


def create_client(config: Config) -> Anthropic:
    """Create an Anthropic client.

    Args:
        config: Application configuration.

    Returns:
        An Anthropic client.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError(
            "ANTHROPIC_API_KEY environment variable is required when using the Anthropic provider"
        )
    return Anthropic(
        api_key=api_key,
        timeout=config.request_timeout,
    )


def preload(config: Config, client: Any) -> None:
    """Verify the configured model exists before starting the session.

    Args:
        config: Application configuration.
        client: Anthropic client.

    Returns:
        None.

    Raises:
        anthropic.NotFoundError: If config.model does not exist.
    """
    client.models.retrieve(config.model)


def send_request(
    config: Config,
    client: Anthropic,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> Any:
    """Send a request to the Anthropic Messages API.

    Args:
        config: Application configuration.
        client: Anthropic client.
        system_prompt: System prompt text.
        messages: Conversation messages.

    Returns:
        An Anthropic response. `temperature` is omitted for models in
        `_NO_CUSTOM_TEMPERATURE` that reject a custom value.
    """
    request_kwargs: dict[str, Any] = {
        "model": config.model,
        "system": system_prompt,
        "messages": cast(list[MessageParam], messages),
        "max_tokens": config.max_tokens,
    }
    if config.model not in _NO_CUSTOM_TEMPERATURE:
        request_kwargs["temperature"] = config.temperature

    return client.messages.create(**request_kwargs)


def extract_content(response: Any) -> str:
    """Extract assistant text from an Anthropic response.

    Args:
        response: The response returned by the Anthropic Messages API.

    Returns:
        The combined text content with surrounding whitespace removed.
    """
    text_parts = [
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    ]

    return "".join(text_parts).strip()
