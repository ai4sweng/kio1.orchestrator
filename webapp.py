"""Compatibility import for the API package."""

import sys
from typing import TYPE_CHECKING

from api import webapp

if TYPE_CHECKING:
    from api.webapp import (
        WorkflowConfigurationError,
        _conversation_path,
        _conversation_summary,
        create_app,
    )

__all__ = [
    "WorkflowConfigurationError",
    "_conversation_path",
    "_conversation_summary",
    "create_app",
]

sys.modules[__name__] = webapp
