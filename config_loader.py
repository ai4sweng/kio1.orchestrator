import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    """Application configuration."""

    model: str
    ollama_endpoint: str
    prompt_path: str
    chat_directory: str
    temperature: float


def load_config(config_path: str = "config.json") -> Config:
    """Load configuration from a JSON file.

    Args:
        config_path: Path to the configuration JSON file.

    Returns:
        A `Config` instance populated from the file.
    """
    path = Path(config_path)
    data = json.loads(path.read_text())
    return Config(
        model=data["model"],
        ollama_endpoint=data["ollama_endpoint"],
        prompt_path=data["prompt_path"],
        chat_directory=data["chat_directory"],
        temperature=data["temperature"],
    )
