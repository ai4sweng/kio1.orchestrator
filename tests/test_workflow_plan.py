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


def test_mixed_plan_without_depends_on_falls_back_to_sequential_and_logs_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = make_plan_dict([make_step("s1"), make_step("s2")], execution_mode="mixed")

    with caplog.at_level(logging.INFO, logger="workflow_plan"):
        plan = parse_plan(raw)

    assert plan.steps[1].depends_on == ("s1",)
    fallback = [r for r in caplog.records if "mixed" in r.getMessage()]
    assert fallback and fallback[0].levelno == logging.INFO


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


@pytest.mark.parametrize(
    "workflow_id",
    ["../escape", "wf/child", "wf\\child", "", "wf 01", "wf\n01", "wf01\n", 42, None],
)
def test_unsafe_or_non_string_workflow_id_is_rejected(workflow_id: Any) -> None:
    raw = make_plan_dict([make_step("s1")])
    raw["workflow_id"] = workflow_id

    with pytest.raises(ValueError, match="workflow_id"):
        parse_plan(raw)


@pytest.mark.parametrize("step_id", ["../s1", "s/1", "", "s 1", 1, None])
def test_unsafe_or_non_string_step_id_is_rejected(step_id: Any) -> None:
    raw = make_plan_dict([make_step("s1")])
    raw["steps"][0]["step_id"] = step_id

    with pytest.raises(ValueError, match="step_id"):
        parse_plan(raw)


@pytest.mark.parametrize("agent_id", ["", "KIO 10", "KIO/10", 10, None])
def test_unsafe_or_non_string_agent_id_is_rejected(agent_id: Any) -> None:
    raw = make_plan_dict([make_step("s1")])
    raw["steps"][0]["agent_id"] = agent_id

    with pytest.raises(ValueError, match="agent_id"):
        parse_plan(raw)


@pytest.mark.parametrize("field_name", ["capability", "task"])
@pytest.mark.parametrize("value", ["", "   ", 7, None, ["x"]])
def test_empty_or_non_string_step_text_fields_are_rejected(
    field_name: str, value: Any
) -> None:
    raw = make_plan_dict([make_step("s1")])
    raw["steps"][0][field_name] = value

    with pytest.raises(ValueError, match=field_name):
        parse_plan(raw)


@pytest.mark.parametrize("explanation", [7, None, {"text": "x"}])
def test_non_string_explanation_is_rejected(explanation: Any) -> None:
    raw = make_plan_dict([make_step("s1")])
    raw["explanation"] = explanation

    with pytest.raises(ValueError, match="explanation"):
        parse_plan(raw)


def test_missing_explanation_defaults_to_empty_string() -> None:
    raw = make_plan_dict([make_step("s1")])
    del raw["explanation"]

    assert parse_plan(raw).explanation == ""


def test_identifiers_with_letters_digits_dot_dash_underscore_are_accepted() -> None:
    raw = make_plan_dict([make_step("step_1.a-B")])
    raw["workflow_id"] = "wf-med.sch_01"

    plan = parse_plan(raw)

    assert plan.workflow_id == "wf-med.sch_01"
    assert plan.steps[0].step_id == "step_1.a-B"


def test_depends_on_entries_must_be_strings() -> None:
    raw = make_plan_dict([make_step("s1"), make_step("s2", depends_on=[1])])

    with pytest.raises(ValueError, match="depends_on"):
        parse_plan(raw)


@pytest.mark.parametrize("entry", ["step_id agent_id capability task", 7, None, ["s1"]])
def test_step_entry_that_is_not_an_object_is_rejected(entry: Any) -> None:
    raw = make_plan_dict([make_step("s1")])
    raw["steps"][0] = entry

    with pytest.raises(ValueError, match="steps"):
        parse_plan(raw)


def test_steps_that_is_not_a_list_is_rejected() -> None:
    raw = make_plan_dict([make_step("s1")])
    raw["steps"] = {"s1": make_step("s1")}

    with pytest.raises(ValueError, match="steps"):
        parse_plan(raw)
