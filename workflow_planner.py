"""Compatibility import for the workflow package."""

import sys
from typing import TYPE_CHECKING

from workflow import planner

if TYPE_CHECKING:
    from workflow.planner import (
        WorkflowConfigurationError,
        _NoRedirect,
        _send_task,
        dispatch_workflow,
        load_agent_registry,
        validate_plan,
    )

__all__ = [
    "WorkflowConfigurationError",
    "_NoRedirect",
    "_send_task",
    "dispatch_workflow",
    "load_agent_registry",
    "validate_plan",
]

sys.modules[__name__] = planner
