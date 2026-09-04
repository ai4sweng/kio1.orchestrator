import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_AGENT_ADDRESS_SCHEMES = ("http://", "https://", "stub://")


@dataclass(frozen=True)
class DispatchSettings:
    """Settings for dispatching plan steps to KIO agents.

    Dispatch is off unless ``enabled`` is set. ``agents`` maps an agent id such
    as ``KIO10`` to its address; agents absent from the map are treated as not
    deployed and their steps are skipped.
    """

    enabled: bool = False
    poll_interval_seconds: float = 2.0
    step_timeout_seconds: float = 600.0
    agents: dict[str, str] = field(default_factory=dict)


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
    keep_alive: int
    max_output_tokens: int
    provider_options: dict[str, Any] = field(default_factory=dict)
    dispatch: DispatchSettings = field(default_factory=DispatchSettings)


def load_config(config_path: str = "config.json") -> Config:
    """Load configuration from a JSON file.

    Args:
        config_path: Path to the configuration JSON file.

    Returns:
        A `Config` instance populated from the file.
    """
    path = Path(config_path)
    data: dict[str, Any] = json.loads(path.read_text())

    provider_options = data.get("provider_options", {})

    if not isinstance(provider_options, dict):
        raise ValueError("provider_options must be a JSON object.")

    allowed_providers = frozenset(data["allowed_providers"])
    if data["provider"] not in allowed_providers:
        raise ValueError(f"Unknown provider: {data['provider']!r}")

    keep_alive = data.get("keep_alive", -1)
    if type(keep_alive) is not int:
        raise ValueError("keep_alive must be an integer.")

    max_output_tokens = data.get("max_output_tokens", 4096)
    if type(max_output_tokens) is not int or max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be a positive integer.")

    dispatch = _parse_dispatch(data.get("dispatch"))

    logger.info(
        "Config loaded: provider=%s model=%s prompt_path=%s",
        data["provider"],
        data["model"],
        data["prompt_path"],
    )

    return Config(
        provider=data["provider"],
        allowed_providers=allowed_providers,
        model=data["model"],
        prompt_path=data["prompt_path"],
        chat_directory=data["chat_directory"],
        temperature=data["temperature"],
        request_timeout=data["request_timeout"],
        keep_alive=keep_alive,
        max_output_tokens=max_output_tokens,
        provider_options=provider_options,
        dispatch=dispatch,
    )


def _parse_dispatch(raw: Any) -> DispatchSettings:
    """Validate the optional ``dispatch`` section of the configuration."""
    if raw is None:
        return DispatchSettings()
    if not isinstance(raw, dict):
        raise ValueError("dispatch must be a JSON object.")

    defaults = DispatchSettings()

    enabled = raw.get("enabled", defaults.enabled)
    if not isinstance(enabled, bool):
        raise ValueError("dispatch.enabled must be a boolean.")

    poll_interval = raw.get("poll_interval_seconds", defaults.poll_interval_seconds)
    if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)):
        raise ValueError("dispatch.poll_interval_seconds must be a number.")
    if poll_interval <= 0:
        raise ValueError("dispatch.poll_interval_seconds must be positive.")

    step_timeout = raw.get("step_timeout_seconds", defaults.step_timeout_seconds)
    if isinstance(step_timeout, bool) or not isinstance(step_timeout, (int, float)):
        raise ValueError("dispatch.step_timeout_seconds must be a number.")
    if step_timeout <= 0:
        raise ValueError("dispatch.step_timeout_seconds must be positive.")

    agents = raw.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("dispatch.agents must be a JSON object.")
    for agent_id, address in agents.items():
        if not isinstance(agent_id, str) or not isinstance(address, str):
            raise ValueError("dispatch.agents must map agent ids to address strings.")
        if not address.startswith(_AGENT_ADDRESS_SCHEMES):
            raise ValueError(
                f"dispatch.agents[{agent_id!r}] has unsupported address {address!r}; "
                f"expected one of {', '.join(_AGENT_ADDRESS_SCHEMES)}"
            )

    return DispatchSettings(
        enabled=enabled,
        poll_interval_seconds=float(poll_interval),
        step_timeout_seconds=float(step_timeout),
        agents=dict(agents),
    )
