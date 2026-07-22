import ast
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def format_json(raw_json: str) -> str:
    """Parse and pretty-print a JSON string.

    Falls back to `ast.literal_eval` if standard JSON parsing fails
    (e.g., when the model returns single-quoted Python dicts).

    Args:
        raw_json: A raw JSON string to format.

    Returns:
        A formatted JSON string with 2-space indentation.
    """
    normalized_content = _strip_markdown_fence(raw_json)
    try:
        parsed: Any = json.loads(normalized_content)
    except json.JSONDecodeError:
        logger.warning("Standard JSON parsing failed, falling back to ast.literal_eval")
        parsed = ast.literal_eval(normalized_content)
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def _strip_markdown_fence(content: str) -> str:
    """Remove an optional Markdown code fence.
    
    Args:
    content: Raw text that may be wrapped in a Markdown code fence.

    Returns:
        The content with the surrounding code fence removed, if present;
        otherwise the original stripped content unchanged.
    """
    stripped_content = content.strip()

    if not stripped_content.startswith("```"):
        return stripped_content

    lines = stripped_content.splitlines()

    if len(lines) < 3 or lines[-1].strip() != "```":
        return stripped_content

    return "\n".join(lines[1:-1]).strip()
