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

    Dispatch is off unless `enabled` is set. `agents` maps an agent id such
    as `KIO10` to its address; agents absent from the map are treated as not
    deployed and their steps are skipped. `max_parallel_steps` caps how many
    steps are in flight at once, whatever the dependency graph allows.
    """

    enabled: bool = False
    poll_interval_seconds: float = 2.0
    step_timeout_seconds: float = 600.0
    max_parallel_steps: int = 4
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
    """Validate the optional `dispatch` section of the configuration.

    Args:
        raw: The `dispatch` value from the JSON file, or None when the section
            is absent.

    Returns:
        A `DispatchSettings` instance; the defaults when the section is absent.

    Raises:
        ValueError: If a field has the wrong type, an interval, timeout or
            parallelism limit is not positive, or an agent address uses an
            unsupported scheme.
    """
    if raw is None:
        return DispatchSettings()
    if not isinstance(raw, dict):
        raise ValueError("dispatch must be a JSON object.")

    defaults = DispatchSettings()

    enabled = raw.get("enabled", defaults.enabled)
    if not isinstance(enabled, bool):
        raise ValueError("dispatch.enabled must be a boolean.")

    poll_interval = _positive_number(
        raw, "poll_interval_seconds", defaults.poll_interval_seconds
    )
    step_timeout = _positive_number(
        raw, "step_timeout_seconds", defaults.step_timeout_seconds
    )

    max_parallel_steps = raw.get("max_parallel_steps", defaults.max_parallel_steps)
    if type(max_parallel_steps) is not int or max_parallel_steps <= 0:
        raise ValueError("dispatch.max_parallel_steps must be a positive integer.")

    agents = raw.get("agents", {})
    if not isinstance(agents, dict):
        raise ValueError("dispatch.agents must be a JSON object.")
    for agent_id, address in agents.items():
        if not isinstance(address, str):
            raise ValueError("dispatch.agents must map agent ids to address strings.")
        if not address.startswith(_AGENT_ADDRESS_SCHEMES):
            raise ValueError(
                f"dispatch.agents[{agent_id!r}] has unsupported address {address!r}; "
                f"expected one of {', '.join(_AGENT_ADDRESS_SCHEMES)}"
            )

    return DispatchSettings(
        enabled=enabled,
        poll_interval_seconds=poll_interval,
        step_timeout_seconds=step_timeout,
        max_parallel_steps=max_parallel_steps,
        agents=dict(agents),
    )


def _positive_number(raw: dict[str, Any], name: str, default: float) -> float:
    """Read a positive number from the `dispatch` section.

    Args:
        raw: The `dispatch` section.
        name: The field to read.
        default: The value used when the field is absent.

    Returns:
        The value as a float.

    Raises:
        ValueError: If the value is not a number or is not positive.
    """
    value = raw.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"dispatch.{name} must be a number.")
    if value <= 0:
        raise ValueError(f"dispatch.{name} must be positive.")
    return float(value)
