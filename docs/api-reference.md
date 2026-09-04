# API Reference

```
config_loader --> Config (dataclass)
               --> load_config()

provider_client --> ProviderModule (Protocol)
                --> load_provider()

prompt_loader --> load_prompt()

session_logger --> generate_session_id()
                --> init_logger()
                --> measure_duration()

ollama_client / openai_client / anthropic_client
                --> create_client(config)
                --> preload(config, client)
                --> send_request(config, client, system_prompt, messages)
                --> extract_content(response)

chat_history  --> create_chat_file(chat_directory, system_prompt, session_id)
              --> append_user_message()
              --> append_assistant_message()
              --> load_messages()

formatter     --> format_json()

workflow_plan --> Step, WorkflowPlan (dataclasses)
              --> parse_plan(data)

kio10.transport --> KIO10Transport (Protocol), TransportError
                --> build_request(workflow_id, step, data)
                --> run_job(transport, request, poll_interval)
                --> HttpKIO10, create_transport(address, timeout)

kio10.stub    --> StubKIO10

kio10.dispatcher --> StepResult, DispatchReport (dataclasses)
                 --> run_workflow(plan, settings, transports=None)
                 --> format_report(), write_report(), dispatch_plan()
```

## `config_loader`

### `Config` (dataclass)

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `str` | Provider module name |
| `allowed_providers` | `frozenset[str]` | Providers permitted to load |
| `model` | `str` | Model identifier |
| `prompt_path` | `str` | Path to system prompt file |
| `chat_directory` | `str` | Chat history directory |
| `temperature` | `float` | Sampling temperature |
| `request_timeout` | `float` | HTTP timeout in seconds |
| `keep_alive` | `int` | Ollama residency control (`-1` keeps loaded) |
| `max_output_tokens` | `int` | Maximum generated output tokens |
| `provider_options` | `dict[str, Any]` | Provider-specific configuration |

### `load_config(config_path="config.json") -> Config`

Loads a `Config` from a JSON file. Validates `provider` against `allowed_providers`.

Raises `FileNotFoundError` if the file does not exist. Raises `KeyError` if a required field is missing.

## `provider_client`

### `load_provider(provider_name, allowed_providers) -> ProviderModule`

Dynamically imports `<provider_name>_client.py` and validates it implements `create_client`, `preload`, `send_request`, and `extract_content`.

Raises `ValueError` if `provider_name` is not in `allowed_providers` or is not a valid identifier. Raises `TypeError` if the imported module is missing a required function.

## `prompt_loader`

### `load_prompt(prompt_path) -> str`

Reads and returns the stripped text content of a prompt file.

Raises `FileNotFoundError` if the file does not exist.

## `session_logger`

### `generate_session_id() -> str`

Returns a `<YYYYMMDD>_<HHMMSS>_<8-char-uuid>` id, generated once per run and shared by the session's log file and chat file so the two can be correlated.

### `init_logger(log_directory, session_id) -> logging.Logger`

Records are formatted as UTC ISO 8601 with milliseconds, followed by the session id, level, module, and message. Creates `log_directory` if absent, clears any pre-existing root handlers so repeated calls do not duplicate output, and raises `httpx`, `httpcore`, `openai`, and `anthropic` to `WARNING` to keep third-party request logging out of the session file.

| Handler | Destination | Level |
|---------|-------------|-------|
| `FileHandler` | `logs/log_<session_id>.log` | `DEBUG` |
| `StreamHandler` | stderr | `ERROR` |

### `measure_duration() -> Iterator[Callable[[], int]]`

Context manager measuring wall-clock time with `time.perf_counter()`. Yields a callable returning elapsed whole milliseconds:

```python
with measure_duration() as elapsed:
    response = client.messages.create(**request_kwargs)

logger.info("Response received: duration_ms=%d", elapsed())
```

## Provider modules (`ollama_client`, `openai_client`, `anthropic_client`)

Each provider module implements the same four functions:

### `create_client(config) -> Any`

Creates and returns the provider's SDK client (`None` for Ollama, which uses direct HTTP requests).

### `preload(config, client) -> None`

Performs provider-specific startup work. Ollama loads the model into memory with the configured context window; OpenAI and Anthropic verify the configured model exists via `client.models.retrieve(config.model)`. Each preload operation logs its duration separately from request timing.

### `send_request(config, client, system_prompt, messages) -> Any`

Sends a chat request to the provider and returns the raw response. Raises `ValueError` when the provider reports that generation stopped at a configured token limit.


### `extract_content(response) -> str`

Extracts the assistant's text content from a provider response.


## `chat_history`

### `create_chat_file(chat_directory, system_prompt, session_id=None) -> Path`

Creates a JSONL chat file with the system prompt as the first line.
Filename format: `chat_<session_id>.jsonl`

`session_id` should be the id from `generate_session_id()`, pairing the chat file with the session's log file. When omitted, a fresh id is generated internally.

### `append_user_message(chat_file, message) -> None`

Appends a user message as a JSONL line.

### `append_assistant_message(chat_file, message) -> None`

Appends an assistant message as a JSONL line.

### `load_messages(chat_file) -> list[dict]`

Returns all messages from the chat file, excluding the system prompt.

## `formatter`

### `format_json(raw_json) -> str`

Parses and pretty-prints a JSON string with 2-space indentation. Falls back to `ast.literal_eval` for single-quoted Python dict output from the model.

## `workflow_plan`

### `Step` / `WorkflowPlan` (dataclasses)

`Step` carries `step_id`, `agent_id`, `capability`, `task` and `depends_on` (tuple of step ids). `WorkflowPlan` carries `workflow_id`, `execution_mode`, `steps` and `explanation`.

### `parse_plan(data) -> WorkflowPlan`

Validates a raw plan dict. Derives `depends_on` from `execution_mode` when no step declares it. Raises `ValueError` on missing fields, unknown `execution_mode`, duplicate step ids, unknown dependencies or cycles.

## `kio10.transport`

### `build_request(workflow_id, step, data) -> dict`

Builds the KIO1 → KIO10 request message from the integration document.

### `run_job(transport, request, poll_interval) -> dict`

Submits the request, checks the acknowledgement, then polls `get_job` until the status is `success`, `needs_clarification` or `failure`. Raises `TransportError` when a reply violates the contract.

### `HttpKIO10(base_url, timeout, httpx_transport=None)`

Transport over `POST /jobs` and `GET /jobs/{job_id}` using `httpx2.AsyncClient`. Connection errors, HTTP error statuses and non-JSON bodies become `TransportError`.

### `create_transport(address, timeout) -> KIO10Transport`

Returns `HttpKIO10` for `http://`/`https://` addresses and `StubKIO10` for `stub://`.

## `kio10.stub`

### `StubKIO10(polls_before_done=1)`

In-memory KIO10. `submit` is idempotent per `(workflow_id, step_id)`; `get_job` returns the acknowledgement for `polls_before_done` polls, then a final reply chosen by the `[stub:fail]` / `[stub:clarify]` markers in the task.

## `kio10.dispatcher`

### `run_workflow(plan, settings, transports=None) -> DispatchReport`

Coroutine. Runs every step as an `asyncio` task; a step awaits its dependencies, is skipped when its agent is not in `transports`/`settings.agents` or a dependency did not succeed, and otherwise submits and polls with `settings.step_timeout_seconds` as the limit.

### `format_report(report) -> str`, `write_report(report, log_directory, session_id) -> Path`

Terminal table and JSON file `dispatch_<session_id>_<workflow_id>.json`.

### `dispatch_plan(plan_json, settings, log_directory, session_id) -> str`

Convenience entry point used by `main.py`: parse, run, store, and return the terminal summary.
