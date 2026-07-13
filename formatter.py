import ast
import json
from typing import Any


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
        parsed = ast.literal_eval(normalized_content)
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def _strip_markdown_fence(content: str) -> str:
    """Remove an optional Markdown code fence."""
    stripped_content = content.strip()

    if not stripped_content.startswith("```"):
        return stripped_content

    lines = stripped_content.splitlines()

    if len(lines) < 3 or lines[-1].strip() != "```":
        return stripped_content

    return "\n".join(lines[1:-1]).strip()