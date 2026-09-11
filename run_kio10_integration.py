"""End-to-end integration run: KIO1 -> workflow planner -> KIO10 over HTTP.

Drives the exact turn path of main.py - the planner model's JSON reply is
formatted, parsed by workflow_plan and dispatched by kio10.dispatcher to a
live KIO10 service - with the model reply scripted, because this machine has
no Ollama install or provider key. Everything after the reply is production
code on both sides.

Prerequisite: the KIO10 service is listening (in the KIO10 repo:
`PYTHONPATH=. .venv/bin/python -m kio10.api.server 8010`).

Run: .venv/bin/python run_kio10_integration.py
"""
import asyncio
import json

from config_loader import DispatchSettings
from formatter import format_json
from kio10.dispatcher import format_report, run_workflow
from workflow_plan import parse_plan

KIO10_URL = "http://127.0.0.1:8010"

SETTINGS = DispatchSettings(
    enabled=True,
    poll_interval_seconds=0.2,
    step_timeout_seconds=120,
    max_parallel_steps=4,
    agents={"KIO10": KIO10_URL},
)

DEMO_REFS = {
    "task_model": {"uri": "shm://demo/pim/v1", "schema_id": "task_model/1.0"},
    "target_hw": {"uri": "shm://demo/pcm/s32k344/v1",
                  "schema_id": "device_passport/1.2"},
    "datasets": [{"uri": "shm://demo/dataset/synthetic/v1",
                  "schema_id": "dataset_bundle/1.0"}],
}

TURNS = [
    (
        "Full TinyML pipeline with data references -> expect success",
        {
            "workflow_id": "wf-int-tinyml01",
            "execution_mode": "sequential",
            "steps": [
                {"step_id": "s1", "agent_id": "KIO10", "capability": "tinyml",
                 "task": "Train and compress a battery RUL model for S32K344",
                 "data": DEMO_REFS}
            ],
            "explanation": "Single KIO10 step with seeded references.",
        },
    ),
    (
        "Planner-style plan without data refs -> expect needs_clarification, "
        "dependent step skipped",
        {
            "workflow_id": "wf-int-plain01",
            "execution_mode": "sequential",
            "steps": [
                {"step_id": "s1", "agent_id": "KIO10",
                 "capability": "energy_efficiency",
                 "task": "Analyze energy of the RUL firmware"},
                {"step_id": "s2", "agent_id": "KIO10", "capability": "tinyml",
                 "task": "Retrain under the energy report"},
            ],
            "explanation": "What today's planner prompt would emit: no refs.",
        },
    ),
    (
        "Energy analysis with code and device refs -> expect success",
        {
            "workflow_id": "wf-int-energy01",
            "execution_mode": "sequential",
            "steps": [
                {"step_id": "s1", "agent_id": "KIO10",
                 "capability": "energy_efficiency",
                 "task": "Analyze energy of the demo code bundle",
                 "data": {"code_ref": {"uri": "shm://demo/code/v1",
                                       "schema_id": "code_bundle/1.0"},
                          "target_hw": DEMO_REFS["target_hw"]}}
            ],
            "explanation": "Energy-efficiency capability path.",
        },
    ),
    (
        "Duplicate of the first plan -> same job_id, no second run "
        "(idempotency)",
        {
            "workflow_id": "wf-int-tinyml01",
            "execution_mode": "sequential",
            "steps": [
                {"step_id": "s1", "agent_id": "KIO10", "capability": "tinyml",
                 "task": "Train and compress a battery RUL model for S32K344",
                 "data": DEMO_REFS}
            ],
            "explanation": "Resubmission of the same workflow step.",
        },
    ),
]


def main() -> None:
    job_ids: dict[str, str | None] = {}
    for title, model_reply in TURNS:
        print(f"\n=== {title} ===")
        formatted = format_json(json.dumps(model_reply))
        plan = parse_plan(json.loads(formatted))
        print(f"Plan check: OK, {len(plan.steps)} steps, {plan.execution_mode}")
        report = asyncio.run(run_workflow(plan, SETTINGS))
        print(format_report(report))
        job_ids[title] = report.results[0].job_id

    first = TURNS[0][0]
    duplicate = TURNS[3][0]
    same = job_ids[first] == job_ids[duplicate]
    print(f"\nIdempotency check: first job {job_ids[first]!r}, "
          f"resubmission job {job_ids[duplicate]!r} -> "
          f"{'SAME' if same else 'DIFFERENT'}")
    if not same:
        raise SystemExit("idempotency violated: resubmission started a new job")


if __name__ == "__main__":
    main()
