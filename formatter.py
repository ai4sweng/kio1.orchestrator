import ast
import json


def format_json(raw_json: str) -> str:
    """Parse and pretty-print a JSON string.

    Falls back to `ast.literal_eval` if standard JSON parsing fails
    (e.g., when the model returns single-quoted Python dicts).

    Args:
        raw_json: A raw JSON string to format.

    Returns:
        A formatted JSON string with 2-space indentation.
    """
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        parsed = ast.literal_eval(raw_json)
    return json.dumps(parsed, indent=2, ensure_ascii=False)
