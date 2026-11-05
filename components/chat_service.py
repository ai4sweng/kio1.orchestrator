"""Application service for a single persisted chat turn."""

from pathlib import Path

from ai.ollama_client import PROVIDER_NAME, extract_content, send_request
from chat_history import append_assistant_message, append_user_message, load_messages
from config_loader import Config
from telemetry import trace_gen_ai_request, trace_turn


def run_turn(
    config: Config,
    system_prompt: str,
    chat_file: Path,
    session_id: str,
    turn: int,
    query: str,
) -> str:
    """Send a query to Ollama and persist the exchange.

    Args:
        config: Application configuration.
        system_prompt: Rendered system prompt for the active session.
        chat_file: Path to the session's chat history file.
        session_id: Shared session id used for tracing.
        turn: 1-based turn number, used for tracing.
        query: The user's query text.

    Returns:
        The raw assistant message content (not yet formatted).
    """
    with trace_turn(
        session_id=session_id,
        turn_number=turn,
        provider=PROVIDER_NAME,
        model=config.model,
    ):
        messages = load_messages(chat_file)
        messages.append({"role": "user", "content": query})

        with trace_gen_ai_request(
            provider=PROVIDER_NAME,
            model=config.model,
            session_id=session_id,
            turn_number=turn,
            message_count=len(messages) + 1,
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
        ):
            response = send_request(
                config=config, system_prompt=system_prompt, messages=messages
            )
        content = extract_content(response)

        append_user_message(chat_file, query)
        append_assistant_message(chat_file, content)

    return content
