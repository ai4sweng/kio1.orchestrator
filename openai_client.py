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
    return OpenAI(
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
    """Send a request to the OpenAI Responses API.

    Args:
        config: Application configuration.
        client: OpenAI client.
        system_prompt: System prompt text.
        messages: Conversation messages.

    Returns:
        An OpenAI response.
    """
    return client.responses.create(
        model=config.model,
        input=[
            {"role": "system", "content": system_prompt},
            *messages
        ],
        temperature=config.temperature,
        max_output_tokens=config.max_tokens,
        text={
            "format": {
                "type": "json_object",
            }
        },
    )


def extract_content(response: Any) -> str:
    """Extract assistant text from an OpenAI response."""
    return response.output_text.strip()