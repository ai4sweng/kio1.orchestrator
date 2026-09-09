"""Provider double for integration tests: returns a plan read from a file.

The plan path comes from `provider_options.plan_path`, so each test can
script the model's answer without a network or a running model.
"""

from pathlib import Path
from typing import Any

from config_loader import Config


def create_client(config: Config) -> None:
    """Return None: the fake provider needs no client."""


def preload(config: Config, client: Any) -> None:
    """Nothing to preload."""


def send_request(
    config: Config,
    client: Any,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> str:
    """Return the scripted plan text.

    Args:
        config: Application configuration carrying `provider_options.plan_path`.
        client: Unused.
        system_prompt: Unused.
        messages: Unused.

    Returns:
        The content of the plan file.
    """
    return Path(config.provider_options["plan_path"]).read_text()


def extract_content(response: Any) -> str:
    """Return the scripted plan text unchanged."""
    return str(response)
