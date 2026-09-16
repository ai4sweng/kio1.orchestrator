"""End-to-end integration run: KIO1 -> workflow planner -> KIO10 over HTTP.

Drives the exact turn path of main.py - the planner model's JSON reply is
formatted, parsed by workflow_plan and dispatched by kio10.dispatcher to a
live KIO10 service - with the model reply scripted, because this machine has
no Ollama install or provider key. Everything after the reply is production
code on both sides.

Prerequisite: the KIO10 service is listening (in the KIO10 repo:
`PYTHONPATH=. .venv/bin/python -m kio10.api.server 8010`) at the address
given for KIO10 in `config.json` under `dispatch.agents`.

Run: .venv/bin/python run_kio10_integration.py
"""

import asyncio
import json
from dataclasses import dataclass
from formatter import format_json
from typing import Any

from config_loader import DispatchSettings, load_config
from kio10.dispatcher import format_report, run_workflow
from workflow_plan import parse_plan


def load_settings() -> DispatchSettings:
    """Read the dispatch settings from `config.json`, unchanged.

    The KIO10 address, poll interval, step timeout and parallelism limit all
    come from `config.json`, the same source `main.py` uses. `dispatch.enabled`
    is not consulted: running this script is the explicit request to dispatch.

    Returns:
        The `dispatch` section of `config.json`.

    Raises:
        SystemExit: If `config.json` lists no address for KIO10.
    """
    settings = load_config().dispatch
    if "KIO10" not in settings.agents:
        raise SystemExit("config.json: dispatch.agents has no address for KIO10")
    return settings


DEMO_REFS = {
    "task_model": {"uri": "shm://demo/pim/v1", "schema_id": "task_model/1.0"},
    "target_hw": {
        "uri": "shm://demo/pcm/s32k344/v1",
        "schema_id": "device_passport/1.2",
    },
    "datasets": [
        {"uri": "shm://demo/dataset/synthetic/v1", "schema_id": "dataset_bundle/1.0"}
    ],
}


@dataclass(frozen=True)
class Scenario:
    """One scripted planner reply and the per-step statuses it must produce."""

    title: str
    expected: tuple[str, ...]
    plan: dict[str, Any]


TURNS = [
    Scenario(
        "Full TinyML pipeline with data references -> expect success",
        ("success",),
        {
            "workflow_id": "wf-int-tinyml01",
            "execution_mode": "sequential",
            "steps": [
                {
                    "step_id": "s1",
                    "agent_id": "KIO10",
                    "capability": "tinyml",
                    "task": "Train and compress a battery RUL model for S32K344",
                    "data": DEMO_REFS,
                }
            ],
            "explanation": "Single KIO10 step with seeded references.",
        },
    ),
    Scenario(
        (
            "Planner-style plan without data refs -> expect needs_clarification, "
            "dependent step skipped"
        ),
        ("needs_clarification", "skipped"),
        {
            "workflow_id": "wf-int-plain01",
            "execution_mode": "sequential",
            "steps": [
                {
                    "step_id": "s1",
                    "agent_id": "KIO10",
                    "capability": "energy_efficiency",
                    "task": "Analyze energy of the RUL firmware",
                },
                {
                    "step_id": "s2",
                    "agent_id": "KIO10",
                    "capability": "tinyml",
                    "task": "Retrain under the energy report",
                },
            ],
            "explanation": "What today's planner prompt would emit: no refs.",
        },
    ),
    Scenario(
        "Energy analysis with code and device refs -> expect success",
        ("success",),
        {
            "workflow_id": "wf-int-energy01",
            "execution_mode": "sequential",
            "steps": [
                {
                    "step_id": "s1",
                    "agent_id": "KIO10",
                    "capability": "energy_efficiency",
                    "task": "Analyze energy of the demo code bundle",
                    "data": {
                        "code_ref": {
                            "uri": "shm://demo/code/v1",
                            "schema_id": "code_bundle/1.0",
                        },
                        "target_hw": DEMO_REFS["target_hw"],
                    },
                }
            ],
            "explanation": "Energy-efficiency capability path.",
        },
    ),
    Scenario(
        "Duplicate of the first plan -> same job_id, no second run (idempotency)",
        ("success",),
        {
            "workflow_id": "wf-int-tinyml01",
            "execution_mode": "sequential",
            "steps": [
                {
                    "step_id": "s1",
                    "agent_id": "KIO10",
                    "capability": "tinyml",
                    "task": "Train and compress a battery RUL model for S32K344",
                    "data": DEMO_REFS,
                }
            ],
            "explanation": "Resubmission of the same workflow step.",
        },
    ),
]


def main() -> None:
    settings = load_settings()
    print(
        f"KIO10 at {settings.agents['KIO10']}, poll every "
        f"{settings.poll_interval_seconds:g}s, step timeout "
        f"{settings.step_timeout_seconds:g}s (all from config.json)"
    )
    failures: list[str] = []
    job_ids: dict[str, str | None] = {}
    for scenario in TURNS:
        print(f"\n=== {scenario.title} ===")
        formatted = format_json(json.dumps(scenario.plan))
        plan = parse_plan(json.loads(formatted))
        print(f"Plan check: OK, {len(plan.steps)} steps, {plan.execution_mode}")
        report = asyncio.run(run_workflow(plan, settings))
        print(format_report(report))

        statuses = tuple(result.status for result in report.results)
        if statuses != scenario.expected:
            failures.append(
                f"{scenario.title}: expected {scenario.expected}, got {statuses}"
            )
        job_ids[scenario.title] = report.results[0].job_id

    first, duplicate = TURNS[0].title, TURNS[3].title
    first_job, duplicate_job = job_ids[first], job_ids[duplicate]
    print(
        f"\nIdempotency check: first job {first_job!r}, "
        f"resubmission job {duplicate_job!r}"
    )
    if first_job is None or duplicate_job is None:
        failures.append("idempotency: a job id is missing, nothing to compare")
    elif first_job != duplicate_job:
        failures.append("idempotency violated: resubmission started a new job")

    if failures:
        raise SystemExit("\n".join(["FAILED:", *failures]))
    print("\nAll scenarios passed.")


if __name__ == "__main__":
    main()
