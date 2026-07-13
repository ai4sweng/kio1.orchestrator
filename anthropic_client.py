from typing import Any

from anthropic import Anthropic

from config_loader import Config


def create_client(config: Config) -> Anthropic:
    """Create an Anthropic client.

    Args:
        config: Application configuration.

    Returns:
        An Anthropic client.
    """
    return Anthropic(
        timeout=config.request_timeout,
    )


def preload(config: Config, client: Any) -> None:
    """Perform no preload operation for Anthropic."""
    return None


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
        An Anthropic response.
    """
    return client.messages.create(
        model=config.model,
        system=system_prompt,
        messages=messages,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )


def extract_content(response: Any) -> str:
    """Extract assistant text from an Anthropic response."""
    text_parts = [
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    ]

    return "".join(text_parts).strip()