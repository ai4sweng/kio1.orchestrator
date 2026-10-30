# KIO Agent Contract v1

Share this document with KIO2-KIO13 container developers. KIO1 implements this initial synchronous HTTP contract; real container integration is not yet verified.

## Task Request

Implement `POST /tasks`, receiving `Content-Type: application/json`:

```json
{
  "schema_version": "1.0",
  "workflow_id": "wf-123",
  "step_id": "s1",
  "agent_id": "KIO7",
  "capability": "code_generation",
  "task": "Create a FastAPI CRUD service",
  "data": {"acceptance_criteria": ["All endpoints have tests"]},
  "dependency_results": {},
  "idempotency_key": "<64-character SHA-256 hex digest>",
  "deadline_at": "2026-10-30T12:01:00+00:00"
}
```

- Keep `endpoint` out of the payload. KIO1 resolves it from a trusted registry.
- `schema_version` versions the protocol; reject unsupported versions.
- Validate `agent_id`, `capability`, `task`, and the agent-specific `data` schema. Code generation is **KIO7**, not KIO10. KIO10 handles TinyML/energy efficiency.
- `data` carries structured inputs, constraints, acceptance criteria, or artifact references. Agree each capability's input/output schema before integration.
- `dependency_results` maps prerequisite step IDs to their successful `result`. Only KIO1 schedules dependencies; a container executes one task, not the plan.
- Enforce `deadline_at` (UTC) before starting and during work. KIO1 uses a 60-second socket timeout, not a hard end-to-end execution limit.
- Requests and responses are limited to 1 MiB. Use accessible artifact URIs and checksums for files. KIO1's `assets/...` paths are not automatically accessible inside another container; agree shared storage or an authenticated object store.

## Task Response

Return HTTP **200 only when finished**, echoing all five correlation fields:

```json
{
  "schema_version": "1.0",
  "workflow_id": "wf-123",
  "step_id": "s1",
  "agent_id": "KIO7",
  "idempotency_key": "<same key as request>",
  "status": "succeeded",
  "result": {"artifacts": [{"uri": "s3://artifacts/wf-123/service.zip"}]}
}
```

For a completed task failure, return the same correlation fields with `"status": "failed"` and `"error": {"code": "INVALID_INPUT", "message": "Missing requirements", "retryable": false}`. Successful `result` must be an object. HTTP errors, timeouts, redirects, malformed replies, and HTTP 202 are treated as **unknown outcome**, not proof of failure.

## Reliability and Security

- Require the `Idempotency-Key` header to match the body. Persist keys/results across container restarts, atomically reserve a key before execution, and replay the original terminal response for duplicates without repeating side effects. KIO1 hashes task identity, inputs, and dependency results; deadlines are excluded. Compare duplicate semantic inputs without the changing deadline. Never execute concurrent duplicates. An in-progress duplicate needs operator reconciliation in this initial synchronous protocol.
- KIO1 never automatically retries. It stops scheduling after a failed/unknown task; already-running tasks finish. Reconcile unknown outcomes with the agent before resubmitting. Idempotency does not guarantee exactly-once side effects.
- Use an isolated Docker network. Configure optional bearer authentication via `token_env`; use HTTPS across untrusted networks. Never send secrets in `data`. Authenticate/authorize operators at a reverse proxy before exposing KIO1.
- Log workflow ID, step ID, agent ID, status, and duration, not secrets or payloads. Treat all model tasks and artifact references as untrusted; sandbox execution. The Run confirmation is not authorization for flight, deployment, or other safety-critical actions. Agents need their own policy and operator checks.

## KIO1 Scheduling and Setup

Agent descriptions and exact capability names come from `capabilities.json`, shared by the system prompt and validator. Operators can select a replacement with `KIO1_CAPABILITIES_PATH` and restart KIO1. Coordinate catalog changes with receiver developers; a renamed capability must also be supported by its container.

Plans contain `workflow_id`, `execution_mode`, and `steps`. Each step supplies `step_id`, `agent_id`, `capability`, `task`, optional `data`, and `depends_on`. Sequential plans implicitly depend on the preceding step; parallel plans must be independent; mixed plans require explicit dependency lists (including `[]`). KIO1 rejects invalid capabilities, duplicate IDs, missing dependencies, and cycles before dispatch. It runs batches of at most four ready tasks.

Copy `agents.example.json` to `agents.json`, configure only available agents, and set `KIO1_AGENT_CONFIG=agents.json` before starting KIO1. The registry maps each agent to its full task endpoint and optional bearer-token environment name. Select **Run workflow** below a plan and confirm, or use `POST /api/workflows` with `{"approved": true, "plan": <plan object>}`. Disabled/missing configuration returns 503; bad input 400; oversized input 413; concurrent workflow execution 409. Accepted execution returns 200 with workflow `status` (`succeeded`, `failed`, or `unknown`) and step outcomes; unscheduled steps are `blocked`.

This version has no durable scheduler, cancellation, automatic retries, status polling, or restart recovery. Results appear in the browser response, not saved chat history. Run one KIO1 process; its execution lock is process-local. For tasks longer than 60 seconds, agree a separate asynchronous job/status protocol first.

Before rollout, provide actual endpoints, capability schemas, credentials via environment variables, shared artifact storage, execution-time budgets, and any mandatory operator approvals. Confirm this contract with every container team.
