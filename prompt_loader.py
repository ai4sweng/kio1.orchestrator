import json
import logging
import os
from pathlib import Path
from typing import TypedDict

from session_logger import measure_duration

logger = logging.getLogger(__name__)
DEFAULT_CAPABILITIES_PATH = Path(__file__).with_name("capabilities.json")


class AgentDefinition(TypedDict):
    """Describe one agent's purpose and supported capability names."""

    description: str
    capabilities: list[str]


def load_capabilities(path: str | Path | None = None) -> dict[str, AgentDefinition]:
    """Read and validate the shared, replaceable KIO capability catalog.

    Args:
        path: Explicit JSON path; otherwise use KIO1_CAPABILITIES_PATH or the
            bundled capabilities.json file.

    Returns:
        Agent IDs mapped to descriptions and nonempty lists of unique capabilities.

    Raises:
        ValueError: The catalog cannot be read or contains invalid definitions.
    """
    catalog_path = Path(
        path or os.environ.get("KIO1_CAPABILITIES_PATH") or DEFAULT_CAPABILITIES_PATH
    )
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        if not isinstance(catalog, dict) or not catalog:
            raise ValueError("Catalog must be a nonempty object.")
        allowed_agents = {f"KIO{number}" for number in range(2, 14)}
        for agent_id, definition in catalog.items():
            if agent_id not in allowed_agents or not isinstance(definition, dict):
                raise ValueError("Catalog agents must be KIO2-KIO13 objects.")
            description = definition.get("description")
            capabilities = definition.get("capabilities")
            if not isinstance(description, str) or not description.strip():
                raise ValueError("Each agent needs a nonblank description.")
            if (
                not isinstance(capabilities, list)
                or not capabilities
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in capabilities
                )
                or len(set(capabilities)) != len(capabilities)
            ):
                raise ValueError("Capabilities must be unique, nonblank strings.")
    except (OSError, ValueError) as error:
        raise ValueError(f"Invalid capability catalog: {catalog_path}") from error
    return catalog


def load_prompt(prompt_path: str) -> str:
    """Load a system prompt from a text file.

    Args:
        prompt_path: Path to the prompt template. A {{KIO_CAPABILITIES}}
            placeholder is expanded using the shared capability catalog.

    Returns:
        Stripped prompt text with any capability placeholder expanded.

    Raises:
        OSError: The prompt cannot be read.
        ValueError: A referenced capability catalog is invalid.
    """
    with measure_duration() as elapsed:
        content = Path(prompt_path).read_text(encoding="utf-8").strip()
        if "{{KIO_CAPABILITIES}}" in content:
            catalog = load_capabilities()
            catalog_text = "\n".join(
                f"- **{agent_id}**: {definition['description']} "
                f"Capabilities: {', '.join(definition['capabilities'])}."
                for agent_id, definition in catalog.items()
            )
            content = content.replace("{{KIO_CAPABILITIES}}", catalog_text)

    logger.info(
        "Prompt loaded: path=%s duration_ms=%d chars=%d",
        prompt_path,
        elapsed(),
        len(content),
    )

    return content
