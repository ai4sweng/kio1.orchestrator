import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelemetryConfig:
    """OpenTelemetry export configuration."""

    enabled: bool = False
    service_name: str = "kio1-orchestrator"
    otlp_http_endpoint: str = "http://localhost:4318"
    metric_export_interval_ms: int = 5000
    trace_sample_ratio: float = 1.0


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
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    provider_options: dict[str, Any] = field(default_factory=dict)


def _load_telemetry_config(data: dict[str, Any]) -> TelemetryConfig:
    """Load and validate the optional telemetry configuration.

    Args:
        data: Complete decoded configuration object.

    Returns:
        Validated telemetry settings.

    Raises:
        ValueError: If telemetry settings have invalid types or values.
    """
    telemetry_data = data.get("telemetry", {})

    if not isinstance(telemetry_data, dict):
        raise ValueError("telemetry must be a JSON object.")

    enabled = telemetry_data.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("telemetry.enabled must be a boolean.")

    service_name = telemetry_data.get("service_name", "kio1-orchestrator")
    if not isinstance(service_name, str) or not service_name.strip():
        raise ValueError("telemetry.service_name must be a non-empty string.")

    endpoint = telemetry_data.get(
        "otlp_http_endpoint",
        "http://localhost:4318",
    )
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError(
            "telemetry.otlp_http_endpoint must be a non-empty HTTP(S) URL."
        )

    endpoint = endpoint.strip().rstrip("/")
    parsed_endpoint = urlparse(endpoint)
    if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
        raise ValueError(
            "telemetry.otlp_http_endpoint must be a non-empty HTTP(S) URL."
        )

    export_interval = telemetry_data.get("metric_export_interval_ms", 5000)
    if type(export_interval) is not int or export_interval <= 0:
        raise ValueError(
            "telemetry.metric_export_interval_ms must be a positive integer."
        )

    sample_ratio = telemetry_data.get("trace_sample_ratio", 1.0)
    if type(sample_ratio) not in (int, float) or not 0.0 <= sample_ratio <= 1.0:
        raise ValueError(
            "telemetry.trace_sample_ratio must be a number between 0.0 and 1.0."
        )

    return TelemetryConfig(
        enabled=enabled,
        service_name=service_name.strip(),
        otlp_http_endpoint=endpoint,
        metric_export_interval_ms=export_interval,
        trace_sample_ratio=float(sample_ratio),
    )


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

    telemetry = _load_telemetry_config(data)

    allowed_providers = frozenset(data["allowed_providers"])
    if data["provider"] not in allowed_providers:
        raise ValueError(f"Unknown provider: {data['provider']!r}")

    keep_alive = data.get("keep_alive", -1)
    if type(keep_alive) is not int:
        raise ValueError("keep_alive must be an integer.")

    max_output_tokens = data.get("max_output_tokens", 4096)
    if type(max_output_tokens) is not int or max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be a positive integer.")

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
        telemetry=telemetry,
        provider_options=provider_options,
    )
