"""Compatibility import for the UI package."""

import sys
from typing import TYPE_CHECKING

from ui import webapp

if TYPE_CHECKING:
    from ui.webapp import (
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
