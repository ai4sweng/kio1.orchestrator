import logging
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

logger = logging.getLogger(__name__)

EXECUTION_MODES = frozenset({"sequential", "parallel", "mixed"})
_REQUIRED_STEP_FIELDS = ("step_id", "agent_id", "capability", "task")


@dataclass(frozen=True)
class Step:
    """One step of a workflow plan addressed to a single KIO agent."""

    step_id: str
    agent_id: str
    capability: str
    task: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowPlan:
    """A validated workflow plan whose steps form an acyclic dependency graph."""

    workflow_id: str
    execution_mode: str
    steps: tuple[Step, ...]
    explanation: str


def parse_plan(data: dict[str, Any]) -> WorkflowPlan:
    """Parse and validate a raw workflow plan produced by the planner model.

    When no step declares ``depends_on``, dependencies are derived from
    ``execution_mode``: ``sequential`` chains the steps in order, ``parallel``
    leaves them independent, and ``mixed`` falls back to a chain with a warning
    because the plan gave no information about which steps may run together.

    Args:
        data: The plan as a JSON object.

    Returns:
        A `WorkflowPlan` with explicit dependencies on every step.

    Raises:
        ValueError: If a required field is missing, ``execution_mode`` is
            unknown, a step id is duplicated, a dependency names an unknown
            step, or the dependencies form a cycle.
    """
    for field_name in ("workflow_id", "execution_mode", "steps"):
        if field_name not in data:
            raise ValueError(f"Plan is missing required field: {field_name!r}")

    execution_mode = data["execution_mode"]
    if execution_mode not in EXECUTION_MODES:
        raise ValueError(f"Unknown execution_mode: {execution_mode!r}")

    raw_steps = data["steps"]
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("Plan must contain a non-empty list of steps")

    steps = tuple(_parse_step(raw) for raw in raw_steps)
    _reject_duplicate_ids(steps)

    if not any("depends_on" in raw for raw in raw_steps):
        steps = _derive_dependencies(steps, execution_mode)

    _validate_graph(steps)

    return WorkflowPlan(
        workflow_id=data["workflow_id"],
        execution_mode=execution_mode,
        steps=steps,
        explanation=data.get("explanation", ""),
    )


def _parse_step(raw: dict[str, Any]) -> Step:
    for field_name in _REQUIRED_STEP_FIELDS:
        if field_name not in raw:
            raise ValueError(f"Step is missing required field: {field_name!r}")

    depends_on = raw.get("depends_on", [])
    if not isinstance(depends_on, list) or not all(
        isinstance(dep, str) for dep in depends_on
    ):
        raise ValueError(
            f"Step {raw['step_id']!r}: depends_on must be a list of step ids"
        )

    return Step(
        step_id=raw["step_id"],
        agent_id=raw["agent_id"],
        capability=raw["capability"],
        task=raw["task"],
        depends_on=tuple(depends_on),
    )


def _reject_duplicate_ids(steps: tuple[Step, ...]) -> None:
    seen: set[str] = set()
    for step in steps:
        if step.step_id in seen:
            raise ValueError(f"Plan contains duplicate step_id: {step.step_id!r}")
        seen.add(step.step_id)


def _derive_dependencies(
    steps: tuple[Step, ...], execution_mode: str
) -> tuple[Step, ...]:
    if execution_mode == "parallel":
        return steps

    if execution_mode == "mixed":
        logger.warning(
            "Plan uses execution_mode 'mixed' without depends_on; "
            "falling back to sequential execution"
        )

    chained = [steps[0]]
    for previous, step in pairwise(steps):
        chained.append(
            Step(
                step_id=step.step_id,
                agent_id=step.agent_id,
                capability=step.capability,
                task=step.task,
                depends_on=(previous.step_id,),
            )
        )
    return tuple(chained)


def _validate_graph(steps: tuple[Step, ...]) -> None:
    dependencies = {step.step_id: step.depends_on for step in steps}

    for step_id, deps in dependencies.items():
        for dep in deps:
            if dep not in dependencies:
                raise ValueError(f"Step {step_id!r} depends on unknown step {dep!r}")

    # Depth-first search with three colours: unvisited, on the current path, done.
    on_path: set[str] = set()
    done: set[str] = set()

    def visit(step_id: str) -> None:
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
