"""End-to-end integration run: KIO1 -> workflow planner -> KIO2 over HTTP.

Drives the same turn path as `run_kio10_integration.py`: the planner model's
JSON reply is scripted, then formatted, parsed by workflow_plan and dispatched
by kio10.dispatcher to a live KIO2 service. Everything after the reply is
production code on both sides; KIO2 speaks the same job contract as KIO10
(`POST /jobs`, `GET /jobs/{job_id}`), so the dispatcher needs no change.

KIO2 records a real execution, so its steps carry references to the code to
run: the repository and the entry point as `file://` URIs. The programs are the
two examples bundled with KIO2, one failing and one corrected. KIO2 and this
script must see the same file system (both on the host, or the paths mounted
into the KIO2 container).

Prerequisite: the KIO2 service is listening (in the KIO2 repo:
`cd src && python -m kio2.main`, port 8102) at the address given for KIO2 in
`config.json` under `dispatch.agents`. `KIO2_REPO` points at the KIO2 checkout
when it is not the sibling directory `../AI4SWENG-KIO2`.

Run: .venv/bin/python run_kio2_integration.py
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from formatter import format_json
from pathlib import Path
from typing import Any

from config_loader import DispatchSettings, load_config
from kio10.dispatcher import DispatchReport, format_report, run_workflow, write_report
from workflow_plan import parse_plan


def load_settings() -> DispatchSettings:
    """Read the dispatch settings from `config.json`, unchanged.

    Returns:
        The `dispatch` section of `config.json`.

    Raises:
        SystemExit: If `config.json` lists no address for KIO2.
    """
    settings = load_config().dispatch
    if "KIO2" not in settings.agents:
        raise SystemExit("config.json: dispatch.agents has no address for KIO2")
    return settings


def examples_dir() -> Path:
    """Locate the example programs bundled with KIO2.

    Raises:
        SystemExit: If the KIO2 checkout cannot be found.
    """
    repo = Path(os.environ.get("KIO2_REPO", Path(__file__).parent.parent / "AI4SWENG-KIO2"))
    examples = repo.resolve() / "src" / "kio2" / "examples"
    if not (examples / "buggy_order_total.py").is_file():
        raise SystemExit(f"KIO2 examples not found under {examples}; set KIO2_REPO")
    return examples


EXAMPLES = examples_dir()


def code_refs(script: str) -> dict[str, Any]:
    """The `data` references of a KIO2 step that records `script`."""
    return {
        "repository": {"uri": EXAMPLES.as_uri(), "schema_id": "source_repository/1.0"},
        "entry_point": {
            "uri": (EXAMPLES / script).as_uri(),
            "schema_id": "python_script/1.0",
        },
    }


@dataclass(frozen=True)
class Scenario:
    """One scripted planner reply and the per-step statuses it must produce."""

    title: str
    expected: tuple[str, ...]
    plan: dict[str, Any]


TURNS = [
    Scenario(
        "Failing program: localise the fault, then replay the recording -> expect success",
        ("success", "success"),
        {
            "workflow_id": "wf-int-kio2-bug01",
            "execution_mode": "sequential",
            "steps": [
                {
                    "step_id": "s1",
                    "agent_id": "KIO2",
                    "capability": "bug_localization",
                    "task": "Locate the fault behind the failing order-total run",
                    "data": code_refs("buggy_order_total.py"),
                },
                {
                    "step_id": "s2",
                    "agent_id": "KIO2",
                    "capability": "replay",
                    "task": "Open the recorded failure for inspection",
                    "depends_on": ["s1"],
                },
            ],
            "explanation": "Localisation, then replay of the trace artifact it produced.",
        },
    ),
    Scenario(
        "Corrected program -> expect success with verdict clean",
        ("success",),
        {
            "workflow_id": "wf-int-kio2-clean01",
            "execution_mode": "sequential",
            "steps": [
                {
                    "step_id": "s1",
                    "agent_id": "KIO2",
                    "capability": "bug_localization",
                    "task": "Check the corrected order-total program",
                    "data": code_refs("fixed_order_total.py"),
                }
            ],
            "explanation": "A healthy program is a successful step, not an error.",
        },
    ),
    Scenario(
        (
            "Failing and corrected runs recorded in parallel, then aligned "
            "statement by statement -> expect success"
        ),
        ("success", "success", "success"),
        {
            "workflow_id": "wf-int-kio2-align01",
            "execution_mode": "mixed",
            "steps": [
                {
                    "step_id": "s1",
                    "agent_id": "KIO2",
                    "capability": "bug_localization",
                    "task": "Record the failing run",
                    "data": code_refs("buggy_order_total.py"),
                    "depends_on": [],
                },
                {
                    "step_id": "s2",
                    "agent_id": "KIO2",
                    "capability": "bug_localization",
                    "task": "Record the corrected run",
                    "data": code_refs("fixed_order_total.py"),
                    "depends_on": [],
                },
                {
                    "step_id": "s3",
                    "agent_id": "KIO2",
                    "capability": "trace_alignment",
                    "task": "Compare the two executions",
                    "depends_on": ["s1", "s2"],
                },
            ],
            "explanation": "Two recordings reach the comparison as trace artifacts.",
        },
    ),
    Scenario(
        (
            "Planner-style plan without code refs -> expect failure "
            "(entry_point_missing), dependent step skipped"
        ),
        ("failure", "skipped"),
        {
            "workflow_id": "wf-int-kio2-plain01",
            "execution_mode": "sequential",
            "steps": [
                {
                    "step_id": "s1",
                    "agent_id": "KIO2",
                    "capability": "bug_localization",
                    "task": "Locate the null pointer exception in the payment module",
                },
                {
                    "step_id": "s2",
                    "agent_id": "KIO2",
                    "capability": "diagnosis",
                    "task": "Explain the root cause",
                },
            ],
            "explanation": "What today's planner prompt would emit: no refs.",
        },
    ),
    Scenario(
        "fix_recommendation, as in the current system prompt -> expect failure",
        ("failure",),
        {
            "workflow_id": "wf-int-kio2-fix01",
            "execution_mode": "sequential",
            "steps": [
                {
                    "step_id": "s1",
                    "agent_id": "KIO2",
                    "capability": "fix_recommendation",
                    "task": "Recommend a fix for the failing order total",
                    "data": code_refs("buggy_order_total.py"),
                }
            ],
            "explanation": "Fix generation is KIO7's step (D2.6 UC-UC1-03).",
        },
    ),
]


def describe(report: DispatchReport, agent_address: str) -> None:
    """Print what each KIO2 reply says, beyond the one-line status."""
    for result in report.results:
        reply = result.reply or {}
        output = reply.get("output") or {}
        if result.status == "success":
            line = output.get("summary", "")
            if output.get("verdict"):
                line += f"  [verdict={output['verdict']}]"
            print(f"    {result.step_id}: {line}")
            for finding in output.get("findings", [])[:3]:
                print(
                    f"        {finding['file']}:{finding['line']} "
                    f"{finding['dependency']:<9} {finding['confidence']:.2f}  "
                    f"{finding['description']}"
                )
            for d in output.get("divergences", []):
                # `side` names the trace holding statements the other lacks.
                print(
                    f"        {d['length']} statement(s) only in trace "
                    f"{d['side'].upper()} (its positions {d['start']}..{d['end'] - 1}), "
                    f"after aligned position {d['at']}"
                )
            if output.get("trace_url"):
                print(f"        trace: {agent_address.rstrip('/')}{output['trace_url']}")
        elif result.status == "failure":
            message = (reply.get("diagnostics") or {}).get("message", "")
            print(f"    {result.step_id}: {message[:140]}")


def main() -> None:
    settings = load_settings()
    print(
        f"KIO2 at {settings.agents['KIO2']}, poll every "
        f"{settings.poll_interval_seconds:g}s, step timeout "
        f"{settings.step_timeout_seconds:g}s (all from config.json)"
    )
    print(f"Programs from {EXAMPLES}")
    failures: list[str] = []
    num=1
    for scenario in TURNS:
        print("-" * 80)
        print(f"\n=== Scenario {num}: {scenario.title} ===")
        num += 1
        formatted = format_json(json.dumps(scenario.plan))
        plan = parse_plan(json.loads(formatted))
        print(f"Plan check: OK, {len(plan.steps)} steps, {plan.execution_mode}")
        report = asyncio.run(run_workflow(plan, settings))
        print(format_report(report))
        describe(report, settings.agents["KIO2"])
        print(f"Full replies: {write_report(report, 'logs', 'kio2-integration')}")

        statuses = tuple(result.status for result in report.results)
        if statuses != scenario.expected:
            failures.append(
                f"{scenario.title}: expected {scenario.expected}, got {statuses}"
            )

    if failures:
        raise SystemExit("\n".join(["FAILED:", *failures]))
    print("\nAll scenarios passed.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
