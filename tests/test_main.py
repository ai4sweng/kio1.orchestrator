"""End-to-end tests: `main()` with a scripted provider and scripted input."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import main as main_module

TESTS_DIR = Path(__file__).parent

MIXED_PLAN: dict[str, Any] = {
    "workflow_id": "wf-e2e01",
    "execution_mode": "mixed",
    "steps": [
        {"step_id": "s1", "agent_id": "KIO10", "capability": "tinyml", "task": "a"},
        {
            "step_id": "s2",
            "agent_id": "KIO12",
            "capability": "cybersecurity_validation",
            "task": "b",
        },
        {
            "step_id": "s3",
            "agent_id": "KIO10",
            "capability": "energy_efficiency",
            "task": "c",
            "depends_on": ["s1"],
        },
    ],
    "explanation": "e2e",
}


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """Undo the root logger reconfiguration done by `main()`.

    Returns:
        An iterator that yields once, restoring handlers on teardown.
    """
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level

    yield

    for handler in root.handlers[:]:
        if handler not in original_handlers:
            handler.close()
    root.handlers = original_handlers
    root.setLevel(original_level)


def run_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    plan: Any,
    dispatch_enabled: bool,
) -> str:
    """Run `main()` once in a scratch directory with a scripted plan.

    Args:
        tmp_path: Scratch directory used as the working directory.
        monkeypatch: Fixture for redirecting input, sys.path and cwd.
        capsys: Fixture capturing the terminal output.
        plan: The plan the fake provider returns, serialised as JSON.
        dispatch_enabled: Value of `dispatch.enabled` in the written config.

    Returns:
        Everything printed to stdout.
    """
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    (tmp_path / "prompt.txt").write_text("You are KIO1.")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "provider": "fake",
                "allowed_providers": ["fake"],
                "model": "none",
                "prompt_path": "prompt.txt",
                "chat_directory": "chats",
                "temperature": 0.1,
                "request_timeout": 5,
                "provider_options": {"plan_path": str(tmp_path / "plan.json")},
                "dispatch": {
                    "enabled": dispatch_enabled,
                    "poll_interval_seconds": 0.01,
                    "step_timeout_seconds": 5,
                    "agents": {"KIO10": "stub://"},
                },
            }
        )
    )
    monkeypatch.syspath_prepend(str(TESTS_DIR))
    monkeypatch.chdir(tmp_path)
    inputs = iter(["build battery firmware", "exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

    main_module.main()

    return capsys.readouterr().out


def test_prints_plan_and_check_verdict_without_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = run_main(tmp_path, monkeypatch, capsys, MIXED_PLAN, dispatch_enabled=False)

    printed_plan = out[out.index("{") : out.rindex("}") + 1]
    assert json.loads(printed_plan) == MIXED_PLAN, "model output is printed unchanged"
    assert "Plan check: OK, 3 steps, mixed" in out
    assert "Dispatch report" not in out
    assert not list((tmp_path / "logs").glob("dispatch_*.json"))
    chat_lines = (
        next((tmp_path / "chats").glob("chat_*.jsonl")).read_text().splitlines()
    )
    assert [json.loads(line)["role"] for line in chat_lines] == [
        "system",
        "user",
        "assistant",
    ]


def test_dispatches_plan_and_writes_report_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = run_main(tmp_path, monkeypatch, capsys, MIXED_PLAN, dispatch_enabled=True)

    assert "Plan check: OK, 3 steps, mixed" in out
    assert "Dispatch report: wf-e2e01" in out
    assert "Summary: success=2, skipped=1" in out
    report_path = next((tmp_path / "logs").glob("dispatch_*_wf-e2e01.json"))
    report = json.loads(report_path.read_text())
    assert [(s["step_id"], s["status"]) for s in report["steps"]] == [
        ("s1", "success"),
        ("s2", "skipped"),
        ("s3", "success"),
    ]
    assert f"Report saved to logs/{report_path.name}" in out


def test_invalid_plan_is_reported_and_not_dispatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = {**MIXED_PLAN, "steps": [{**MIXED_PLAN["steps"][2]}]}

    out = run_main(tmp_path, monkeypatch, capsys, broken, dispatch_enabled=True)

    assert "Plan check: FAILED, " in out
    assert "unknown step 's1'" in out
    assert "Dispatch report" not in out
    assert not list((tmp_path / "logs").glob("dispatch_*.json"))
