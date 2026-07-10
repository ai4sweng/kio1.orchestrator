import json
import uuid
from datetime import datetime
from pathlib import Path


def create_chat_file(chat_directory: str, system_prompt: str) -> Path:
    """Create a new chat history JSONL file with the system prompt as the first line.

    Args:
        chat_directory: Directory where chat files are stored.
        system_prompt: The system prompt to write as the first line.

    Returns:
        The `Path` to the newly created chat file.
    """
    directory = Path(chat_directory)
    directory.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    filepath = directory / f"chat_{timestamp}_{short_id}.jsonl"
    line = json.dumps({"role": "system", "content": system_prompt})
    filepath.write_text(line + "\n")
    return filepath


def append_user_message(chat_file: Path, message: str) -> None:
    """Append a user message to the chat history file.

    Args:
        chat_file: Path to the chat history file.
        message: The user's message to append.

    Returns:
        None.
    """
    line = json.dumps({"role": "user", "content": message})
    with chat_file.open("a") as f:
        f.write(line + "\n")


def append_assistant_message(chat_file: Path, message: str) -> None:
    """Append an assistant message to the chat history file.

    Args:
        chat_file: Path to the chat history file.
        message: The assistant's response to append.

    Returns:
        None.
    """
    line = json.dumps({"role": "assistant", "content": message})
    with chat_file.open("a") as f:
        f.write(line + "\n")


def load_messages(chat_file: Path) -> list[dict[str, str]]:
    """Load conversation messages from a chat file (excluding the system prompt).

    Args:
        chat_file: Path to the chat history file.

    Returns:
        A list of message dicts with `role` and `content` keys.
    """
    lines = chat_file.read_text().strip().split("\n")
    messages: list[dict[str, str]] = []

    for line in lines[1:]:
        messages.append(json.loads(line))

    return messages
