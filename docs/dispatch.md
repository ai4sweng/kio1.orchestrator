# Dispatch

Dispatch turns a workflow plan into execution messages, sends each step to the
responsible KIO agent, matches the replies back to their steps and reports what
succeeded, what failed, what was skipped and how long each step took.

Dispatch is **off by default**. With `dispatch.enabled: false` the terminal
application behaves exactly as before: it prints the plan and nothing else.

## Enabling

```json
"dispatch": {
    "enabled": true,
    "poll_interval_seconds": 2,
    "step_timeout_seconds": 600,
    "agents": {
        "KIO10": "stub://"
    }
}
```

| Field | Description |
|-------|-------------|
| `enabled` | Run the plan after printing it |
| `poll_interval_seconds` | Pause between polls of a running job |
| `step_timeout_seconds` | Maximum time for one step, from submission to final reply |
| `agents` | Agent id to address. `http://` or `https://` talks to a real endpoint, `stub://` uses the in-memory stub |

Agents missing from `agents` are treated as **not deployed**: their steps are
reported as `skipped`, not as errors.

## Step dependencies

A step may declare the steps whose results it needs:

```json
{"step_id": "s4", "agent_id": "KIO8", "capability": "deployment",
 "task": "Deploy", "depends_on": ["s2", "s3"]}
```

The dispatcher runs each step as its own `asyncio` task. A task waits for the
tasks it depends on, then submits its request. Independent steps therefore run
together and dependent steps wait for exactly their predecessors. This is what
makes `execution_mode: "mixed"` meaningful.

When no step declares `depends_on`, dependencies are derived from
`execution_mode`: `sequential` chains the steps in order, `parallel` leaves them
independent, and `mixed` falls back to a chain with a warning in the log.

Plans with an unknown dependency, a duplicate `step_id` or a dependency cycle
are rejected before anything is sent.

## Passing results between steps

When a dependency finishes with `success`, every entry of its `artifacts` is
placed into the `data` of the dependent step's request under the artifact name,
as `{ "uri": <receipt.uri>, "schema_id": <schema_id> }`. If two dependencies
produce the same artifact name, later ones are prefixed with their step id
(`s2.energy_report`).

A step whose dependency did not finish with `success` is `skipped`.

## Contract with KIO10

KIO10 lives in [ai4sweng/AI4SWENG-KIO10](https://github.com/ai4sweng/AI4SWENG-KIO10); the table of all agents and their integration status is in the [README](../README.md#kio-agents).

The only agreed message contract is the one in the KIO1 – KIO10 integration
document. The dispatcher sends exactly that request and accepts exactly those
replies:

- **Request** (`POST /jobs`): `schema_version`, `workflow_id`, `step_id`,
  `capability`, `task`, `data`.
- **Acknowledgement**: the same envelope with `job_id` and `status: "accepted"`.
- **Final reply** (`GET /jobs/{job_id}`): `status` is `success` (with
  `artifacts`), `needs_clarification` (with `clarification`) or `failure` (with
  `failure_class` and `diagnostics`).

One rule is added so that KIO1 can poll: **while a job is still running,
`GET /jobs/{job_id}` returns the acknowledgement** (`status: "accepted"`).
No new status or field is introduced.

Replies are matched to their step by `workflow_id` and `step_id`. A reply that
names a different step, an acknowledgement without `job_id`, or an unknown
status is treated as a transport error.

The reply format for the other agents (KIO2 – KIO13) is not yet agreed. Until
it is, those agents are not deployed and their steps are skipped; the
dispatcher needs no change when they arrive as long as they follow the same
envelope.

## Result statuses

| Status | Meaning |
|--------|---------|
| `success` | The agent finished the work |
| `failure` | The agent reported the work is not feasible |
| `needs_clarification` | The agent asked for more information; the request is recorded, the clarification loop is not yet implemented |
| `skipped` | The agent is not deployed, or a dependency did not succeed |
| `error` | The agent could not be reached, timed out, or violated the contract |

## Report

After the run the terminal shows one line per step:

```
Dispatch report: wf-mix01
  s1    KIO5    skipped                    0 ms  agent KIO5 not deployed
  s2    KIO10   success                  412 ms  artifacts: energy_efficiency_result
Summary: skipped=1, success=1
Report saved to logs/dispatch_<session_id>_wf-mix01.json
```

The JSON file holds the same data plus each agent's full final reply, so a
result can be traced back to the exact message that produced it.

## Stub agent

`kio10/stub.py` is an in-memory KIO10 that speaks the contract without any
network. It acknowledges, reports `accepted` for one poll, then returns
`success` with plausible `shm://` artifact receipts. Two markers in the task
text switch the outcome so that negative paths can be exercised:

| Marker in task | Final reply |
|----------------|-------------|
| `[stub:fail]` | `failure` with `failure_class` and a `diagnostics` receipt |
| `[stub:clarify]` | `needs_clarification` with two options |

Resubmitting the same `workflow_id` and `step_id` returns the existing job,
mirroring the repeat protection required of real agents.

## Out of scope

The clarification loop with the engineer, callbacks from agents to KIO1,
message queues and writing measured metrics to a database are not part of
dispatch yet.
