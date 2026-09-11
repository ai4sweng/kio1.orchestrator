"""Parse and validate workflow plans produced by the KIO1 planner model."""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any

logger = logging.getLogger(__name__)

EXECUTION_MODES = frozenset({"sequential", "parallel", "mixed"})

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class Step:
    """One step of a workflow plan addressed to a single KIO agent."""

    step_id: str
    agent_id: str
    capability: str
    task: str
    depends_on: tuple[str, ...] = ()
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowPlan:
    """A validated workflow plan whose steps form an acyclic dependency graph."""

    workflow_id: str
    execution_mode: str
    steps: tuple[Step, ...]
    explanation: str


def parse_plan(data: dict[str, Any]) -> WorkflowPlan:
    """Parse and validate a raw workflow plan produced by the planner model.

    When no step declares `depends_on`, dependencies are derived from
    `execution_mode`: `sequential` chains the steps in order, `parallel`
    leaves them independent, and `mixed` falls back to a chain, noted in the
    log, because the plan gave no information about which steps may run together.

    Args:
        data: The plan as a JSON object.

    Returns:
        A `WorkflowPlan` with explicit dependencies on every step.

    Raises:
        ValueError: If a required field is missing or has the wrong type,
            `workflow_id`, `step_id` or `agent_id` contain characters unsafe
            for file names and uris, `execution_mode` is unknown, a step id is
            duplicated, a dependency names an unknown step, or the
            dependencies form a cycle.
    """
    workflow_id = _require_identifier(data.get("workflow_id"), "workflow_id", "Plan")
    explanation = data.get("explanation", "")
    if not isinstance(explanation, str):
        raise ValueError("Plan field 'explanation' must be a string")

    execution_mode = data.get("execution_mode")
    if execution_mode not in EXECUTION_MODES:
        raise ValueError(f"Unknown execution_mode: {execution_mode!r}")

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("Plan must contain a non-empty list of steps")
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise ValueError(f"Plan steps[{index}] must be a JSON object")

    steps = tuple(_parse_step(raw) for raw in raw_steps)

    if not any("depends_on" in raw for raw in raw_steps):
        steps = _derive_dependencies(steps, execution_mode)

    _validate_graph(steps)

    return WorkflowPlan(
        workflow_id=workflow_id,
        execution_mode=execution_mode,
        steps=steps,
        explanation=explanation,
    )


def _require_identifier(value: Any, field_name: str, context: str) -> str:
    """Return `value` if it is a string safe to use in file names and uris.

    Args:
        value: The raw field value from the plan.
        field_name: The field being checked, for the error message.
        context: What holds the field, for the error message.

    Returns:
        The validated identifier.

    Raises:
        ValueError: If the value is not a string or contains characters other
            than letters, digits, `.`, `_` and `-`, or is `.` or `..`.
    """
    if (
        not isinstance(value, str)
        or not _SAFE_IDENTIFIER.fullmatch(value)
        or value in {".", ".."}
    ):
        raise ValueError(
            f"{context} field {field_name!r} must be a non-empty string of letters, "
            f"digits, '.', '_' or '-', got {value!r}"
        )
    return value


def _require_text(value: Any, field_name: str, context: str) -> str:
    """Return `value` if it is a non-blank string.

    Args:
        value: The raw field value from the plan.
        field_name: The field being checked, for the error message.
        context: What holds the field, for the error message.

    Returns:
        The validated text.

    Raises:
        ValueError: If the value is not a string or is blank.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{context} field {field_name!r} must be a non-empty string, got {value!r}"
        )
    return value


def _parse_step(raw: dict[str, Any]) -> Step:
    """Build a `Step` from one raw entry of the plan's `steps` list.

    Args:
        raw: The step as a JSON object.

    Returns:
        The step, with `depends_on` as declared or empty when absent.

    Raises:
        ValueError: If a required field is missing or invalid, or `depends_on`
            is not a list of step ids.
    """
    step_id = _require_identifier(raw.get("step_id"), "step_id", "Step")
    context = f"Step {step_id!r}"
    agent_id = _require_identifier(raw.get("agent_id"), "agent_id", context)
    capability = _require_text(raw.get("capability"), "capability", context)
    task = _require_text(raw.get("task"), "task", context)

    depends_on = raw.get("depends_on", [])
    if not isinstance(depends_on, list) or not all(
        isinstance(dep, str) for dep in depends_on
    ):
        raise ValueError(f"{context}: depends_on must be a list of step ids")

    return Step(
        step_id=step_id,
        agent_id=agent_id,
        capability=capability,
        task=task,
        depends_on=tuple(depends_on),
        data=_parse_data(raw.get("data", {}), context),
    )


def _parse_data(raw: Any, context: str) -> Mapping[str, Any]:
    """Validate a step's optional input references.

    Args:
        raw: The step's `data` value from the plan.
        context: What holds the field, for error messages.

    Returns:
        The references, one entry per input name: a reference object with a
        string `uri`, or a list of such objects (the contract's `datasets`).

    Raises:
        ValueError: If `data` is not an object, or an entry is neither a
            reference object with a non-empty string `uri` (plus an optional
            string `schema_id`) nor a list of them.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{context}: data must be a JSON object")
    for name, reference in raw.items():
        entry = f"{context}: data entry {name!r}"
        references = reference if isinstance(reference, list) else [reference]
        for item in references:
            if not isinstance(item, dict):
                raise ValueError(f"{entry} must be a JSON object or a list "
                                 "of them")
            uri = item.get("uri")
            if not isinstance(uri, str) or not uri.strip():
                raise ValueError(f"{entry} needs a non-empty string uri")
            schema_id = item.get("schema_id")
            if schema_id is not None and not isinstance(schema_id, str):
                raise ValueError(f"{entry}: schema_id must be a string")
    return raw


def _derive_dependencies(
    steps: tuple[Step, ...], execution_mode: str
) -> tuple[Step, ...]:
    """Fill in `depends_on` from `execution_mode` when the plan declared none.

    Args:
        steps: The parsed steps, all with empty `depends_on`.
        execution_mode: One of `sequential`, `parallel` or `mixed`.

    Returns:
        The steps unchanged for `parallel`; otherwise each step depends on the
        previous one. `mixed` is noted in the log because the plan gave no
        information about which steps may run together.
    """
    if execution_mode == "parallel":
        return steps

    if execution_mode == "mixed":
        logger.info(
            "Plan uses execution_mode 'mixed' without depends_on; "
            "falling back to sequential execution"
        )

    chained = [steps[0]]
    chained.extend(
        replace(step, depends_on=(previous.step_id,))
        for previous, step in pairwise(steps)
    )
    return tuple(chained)


def _validate_graph(steps: tuple[Step, ...]) -> None:
    """Check that step ids are unique, dependencies are known and there is no cycle.

    Args:
        steps: The steps with explicit `depends_on`.

    Raises:
        ValueError: If a step id is duplicated, a dependency names an unknown
            step, or the graph has a cycle.
    """
    dependencies: dict[str, tuple[str, ...]] = {}
    for step in steps:
        if step.step_id in dependencies:
            raise ValueError(f"Plan contains duplicate step_id: {step.step_id!r}")
        dependencies[step.step_id] = step.depends_on

    for step_id, deps in dependencies.items():
        for dep in deps:
            if dep not in dependencies:
                raise ValueError(f"Step {step_id!r} depends on unknown step {dep!r}")

    # Depth-first search with three colours: unvisited, on the current path, done.
    on_path: set[str] = set()
    done: set[str] = set()

    def visit(step_id: str) -> None:
        """Visit `step_id` depth-first, raising when a step is already on the path."""
        if step_id in done:
            return
        if step_id in on_path:
            raise ValueError(f"Plan dependencies form a cycle at step {step_id!r}")
        on_path.add(step_id)
        for dep in dependencies[step_id]:
            visit(dep)
        on_path.remove(step_id)
        done.add(step_id)

    for step_id in dependencies:
        visit(step_id)
