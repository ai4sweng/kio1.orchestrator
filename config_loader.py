import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Config:
    """Application configuration."""

    model: str
    ollama_endpoint: str
    prompt_path: str
    chat_directory: str
    temperature: float
    request_timeout: int
    keep_alive: int


def load_config(config_path: str = "config.json") -> Config:
    """Load configuration from a JSON file.

    Args:
        config_path: Path to the configuration JSON file.

    Returns:
        A `Config` instance populated from the file.
    """
    path = Path(config_path)
    data: dict[str, Any] = json.loads(path.read_text())

    keep_alive = data.get("keep_alive", -1)
    if type(keep_alive) is not int:
        raise ValueError("keep_alive must be an integer.")

    return Config(
        model=data["model"],
        ollama_endpoint=data["ollama_endpoint"],
        prompt_path=data["prompt_path"],
        chat_directory=data["chat_directory"],
        temperature=data["temperature"],
        request_timeout=data["request_timeout"],
        keep_alive=keep_alive,
    )
