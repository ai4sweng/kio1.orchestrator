import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Config:
    """Application configuration."""

    provider: str
    allowed_providers: frozenset[str]
    model: str
    prompt_path: str
    chat_directory: str
    temperature: float
    request_timeout: float
    max_tokens: int
    provider_options: dict[str, Any] = field(default_factory=dict)


def load_config(config_path: str = "config.json") -> Config:
    """Load configuration from a JSON file.

    Args:
        config_path: Path to the configuration JSON file.

    Returns:
        A `Config` instance populated from the file.
    """
    path = Path(config_path)
    data = json.loads(path.read_text())

    provider_options = data.get("provider_options", {})

    if not isinstance(provider_options, dict):
        raise ValueError("provider_options must be a JSON object.")

    allowed_providers = frozenset(data["allowed_providers"])
    if data["provider"] not in allowed_providers:
        raise ValueError(f"Unknown provider: {data['provider']!r}")

    return Config(
        provider=data["provider"],
        allowed_providers=allowed_providers,
        model=data["model"],
        prompt_path=data["prompt_path"],
        chat_directory=data["chat_directory"],
        temperature=data["temperature"],
        request_timeout=data["request_timeout"],
        max_tokens=data["max_tokens"],
        provider_options=provider_options,
    )
