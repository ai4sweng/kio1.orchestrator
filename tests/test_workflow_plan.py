import logging
from typing import Any

import pytest

from workflow_plan import Step, WorkflowPlan, parse_plan


def make_plan_dict(
    steps: list[dict[str, Any]], execution_mode: str = "sequential"
) -> dict[str, Any]:
    """Build a raw plan dict as the model would emit it."""
    return {
        "workflow_id": "wf-test01",
        "execution_mode": execution_mode,
        "steps": steps,
        "explanation": "test plan",
    }


def make_step(step_id: str, agent_id: str = "KIO10", **extra: Any) -> dict[str, Any]:
    """Build a raw step dict."""
    step = {
        "step_id": step_id,
        "agent_id": agent_id,
        "capability": "energy_efficiency",
        "task": f"task {step_id}",
    }
    step.update(extra)
    return step


def test_parse_plan_returns_typed_plan_with_explicit_dependencies() -> None:
    raw = make_plan_dict(
        [
            make_step("s1"),
            make_step("s2", depends_on=["s1"]),
            make_step("s3", depends_on=["s1", "s2"]),
        ],
        execution_mode="mixed",
    )

    plan = parse_plan(raw)

    assert isinstance(plan, WorkflowPlan)
    assert plan.workflow_id == "wf-test01"
    assert plan.execution_mode == "mixed"
    assert plan.explanation == "test plan"
    assert [s.step_id for s in plan.steps] == ["s1", "s2", "s3"]
    assert plan.steps[0] == Step(
        step_id="s1",
        agent_id="KIO10",
        capability="energy_efficiency",
        task="task s1",
        depends_on=(),
    )
    assert plan.steps[1].depends_on == ("s1",)
    assert plan.steps[2].depends_on == ("s1", "s2")


def test_sequential_plan_without_depends_on_chains_steps_in_order() -> None:
    raw = make_plan_dict([make_step("s1"), make_step("s2"), make_step("s3")])

    plan = parse_plan(raw)

    assert plan.steps[0].depends_on == ()
    assert plan.steps[1].depends_on == ("s1",)
    assert plan.steps[2].depends_on == ("s2",)


def test_parallel_plan_without_depends_on_has_independent_steps() -> None:
    raw = make_plan_dict([make_step("s1"), make_step("s2")], execution_mode="parallel")

    plan = parse_plan(raw)

    assert all(step.depends_on == () for step in plan.steps)


def test_mixed_plan_without_depends_on_falls_back_to_sequential_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = make_plan_dict([make_step("s1"), make_step("s2")], execution_mode="mixed")

    with caplog.at_level(logging.WARNING, logger="workflow_plan"):
        plan = parse_plan(raw)

    assert plan.steps[1].depends_on == ("s1",)
    assert any("mixed" in record.getMessage() for record in caplog.records)


def test_partial_depends_on_leaves_other_steps_independent() -> None:
    raw = make_plan_dict(
        [make_step("s1"), make_step("s2"), make_step("s3", depends_on=["s1"])],
        execution_mode="mixed",
    )

    plan = parse_plan(raw)

    assert plan.steps[0].depends_on == ()
    assert plan.steps[1].depends_on == ()
    assert plan.steps[2].depends_on == ("s1",)


def test_unknown_dependency_is_rejected() -> None:
    raw = make_plan_dict([make_step("s1", depends_on=["s9"])])

    with pytest.raises(ValueError, match="s9"):
        parse_plan(raw)


def test_dependency_cycle_is_rejected() -> None:
    raw = make_plan_dict(
        [make_step("s1", depends_on=["s2"]), make_step("s2", depends_on=["s1"])],
        execution_mode="mixed",
    )

    with pytest.raises(ValueError, match="cycle"):
        parse_plan(raw)


def test_self_dependency_is_rejected() -> None:
    raw = make_plan_dict([make_step("s1", depends_on=["s1"])])

    with pytest.raises(ValueError, match="cycle"):
        parse_plan(raw)


def test_duplicate_step_id_is_rejected() -> None:
    raw = make_plan_dict([make_step("s1"), make_step("s1")])

    with pytest.raises(ValueError, match="duplicate"):
        parse_plan(raw)


def test_unknown_execution_mode_is_rejected() -> None:
    raw = make_plan_dict([make_step("s1")], execution_mode="random")

    with pytest.raises(ValueError, match="execution_mode"):
        parse_plan(raw)


def test_missing_required_step_field_is_rejected() -> None:
    step = make_step("s1")
    del step["capability"]
    raw = make_plan_dict([step])

    with pytest.raises(ValueError, match="capability"):
        parse_plan(raw)


def test_empty_steps_is_rejected() -> None:
    raw = make_plan_dict([])

    with pytest.raises(ValueError, match="steps"):
        parse_plan(raw)
