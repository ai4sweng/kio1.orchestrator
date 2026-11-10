# Configuration

## Optional Agent Routing

Container launches through `make run` and `make up` use `agents.example.json` by default, enabling dispatch to the KIO2-KIO13 dummy receivers. Override `AGENT_CONFIG` with a trusted registry path to use different agents, or set `AGENT_CONFIG=` to disable dispatch. Local `python main.py` does not automatically load the example registry; set `KIO1_AGENT_CONFIG` explicitly if local workflow dispatch is needed. This is separate from the Ollama configuration.

```json
{
    "KIO7": {
        "endpoint": "https://kio7.internal/tasks",
        "token_env": "KIO7_BEARER_TOKEN"
    }
}
```

`token_env` is optional; when present its environment variable must be nonempty. Never store token values in the registry or model output. In Docker, Make mounts the selected registry read-only and sets `KIO1_AGENT_CONFIG` to its container path. All agents selected by a plan must have registry entries before any task can run. See the [container contract](workflow-contract.md) for scheduling and security details.

## Shared Capability Catalog

`capabilities.json` is the authoritative catalog for both prompt generation and plan validation. Replace its contents or point `KIO1_CAPABILITIES_PATH` to another file. The default path is resolved beside the Python modules, not the current working directory. Explicit paths passed to `load_capabilities(path)` take precedence over the environment variable.

```json
{
    "KIO7": {
        "description": "Code generation and code understanding.",
        "capabilities": ["code_generation", "code_understanding"]
    }
}
```

The catalog must be a nonempty object containing KIO2-KIO13 entries. Each entry needs a nonblank description and a nonempty list of unique, nonblank capability strings. A replacement can contain a subset of agents; routing must reference only agents present in that catalog. Missing or invalid catalogs fail closed.

The bundled prompt expands `{{KIO_CAPABILITIES}}` from this file. Include that placeholder in custom prompts rather than duplicating the catalog. Prompt examples are illustrative; the rendered catalog is authoritative. When changing agent roles, also review any task-ordering instructions specific to those roles.

Restart KIO1 after catalog changes: prompts are loaded at startup. Existing plans must use the active capability names to execute. The bundled catalog is included in the Docker image; rebuild after editing it, or mount a replacement read-only and set `KIO1_CAPABILITIES_PATH` to its container path.

Core model and telemetry settings are stored in `config.json` at the project root.

## Configuration File Reference

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | `ollama` (default), `groq`, or `openrouter` |
| `model` | string | Model identifier recognized by the selected provider |
| `api_key` | string | Hosted API key pasted directly into the local config; non-empty values take precedence over environment credentials |
| `api_key_env` | string | Optional fallback variable name when `api_key` is empty; empty uses `GROQ_API_KEY` or `OPENROUTER_API_KEY` |
| `prompt_path` | string | Path to the system prompt file (relative to project root) |
| `chat_directory` | string | Directory for chat history files |
| `temperature` | float | Sampling temperature (lower = more deterministic) |
| `request_timeout` | float | Request timeout in seconds |
| `keep_alive` | int | Ollama model residency in seconds (`-1` = keep loaded; default: `-1`) |
| `max_output_tokens` | int | Maximum number of generated output tokens; must be positive (default: `4096`) |
| `endpoint` | string | Ollama base URL; ignored by hosted providers, which use fixed HTTPS endpoints |
| `context_window_size` | int | Ollama-only context window; must be larger than `max_output_tokens`; not sent to hosted services |
| `telemetry` | object | Optional OpenTelemetry configuration; telemetry is disabled when omitted |
| `telemetry.enabled` | boolean | Enables trace and metric export (default: `false`) |
| `telemetry.service_name` | string | Non-empty service name attached to exported telemetry (default: `kio1-orchestrator`) |
| `telemetry.otlp_http_endpoint` | string | OpenTelemetry Collector OTLP/HTTP base URL (default: `http://localhost:4318`) |
| `telemetry.otlp_bearer_token` | string | Bearer token sent as `Authorization: Bearer <token>` to the Collector, if it requires authentication (default: `""`, no header sent) |
| `telemetry.metric_export_interval_ms` | int | Positive metric-export interval in milliseconds (default: `5000`) |
| `telemetry.trace_sample_ratio` | number | Fraction of traces sampled, from `0.0` to `1.0` (default: `1.0`) |

## Example `config.json`

```json
{
    "provider": "ollama",
    "api_key": "",
    "api_key_env": "",
    "model": "ministral-3:8b",
    "prompt_path": "prompts/kio1_system_prompt_ver1.txt",
    "chat_directory": "chats",
    "temperature": 0.1,
    "request_timeout": 120,
    "keep_alive": -1,
    "max_output_tokens": 4096,
    "endpoint": "http://localhost:11434",
    "context_window_size": 16384,
    "telemetry": {
        "enabled": false,
        "service_name": "kio1-orchestrator",
        "otlp_http_endpoint": "http://localhost:4318",
        "otlp_bearer_token": "",
        "metric_export_interval_ms": 5000,
        "trace_sample_ratio": 1.0
    }
}
```

## Groq and OpenRouter

Select one provider and its model in `config.json`. Existing configurations without `provider` continue using Ollama. Restart local KIO1 after configuration changes; recreate Docker containers after changing credentials or rebuilding the image.

| Provider | Example model identifier | Default API key environment variable |
|----------|--------------------------|--------------------------------------|
| `ollama` | `ministral-3:8b` | Not required |
| `groq` | `openai/gpt-oss-20b` | `GROQ_API_KEY` |
| `openrouter` | `openai/gpt-4o-mini` | `OPENROUTER_API_KEY` |

Model names are examples, not availability guarantees. Choose a model available to your account that supports chat and JSON-object output. Unsupported models or parameters fail explicitly; there is no automatic model/provider fallback.

For Groq, change these fields while retaining the other shared settings:

```json
"provider": "groq",
"model": "openai/gpt-oss-20b",
"api_key": "gsk_REPLACE_WITH_YOUR_NEW_KEY"
```

Paste your actual key into `api_key` in your local `config.json`, not into this documentation or `config.json.example`. No `export` is needed. Leave `api_key_env` empty when using a direct key; a non-empty `api_key` takes precedence over any environment setting. Keys are stripped of surrounding whitespace, validated before use, and excluded from config representations, provider logs, and error messages.

For OpenRouter, use `"provider": "openrouter"`, set its model identifier, and paste its key into the same `api_key` field.

If you prefer environment credentials, leave `api_key` empty and optionally set `api_key_env` to the variable name (`GROQ_API_KEY` or `OPENROUTER_API_KEY` by default). `api_key_env` is never the key itself. Key-like values in this field are rejected without being echoed in errors when resolving environment credentials. If a key has appeared in a log, terminal transcript, or chat, revoke it and generate a replacement before continuing.

Start locally with `python main.py`, or rebuild with `make build` and launch with `make run` / `make up`. Docker already mounts `config.json` read-only, so direct keys work without additional Docker arguments. The file is excluded from the build context and must remain untracked by Git. Protect its file permissions and backups; the container user (UID 1000) must be able to read it. Never paste a real key into the tracked example file. Optional environment credentials still work: Make forwards the two standard variable names from the invoking shell without printing their values. Custom variable names require explicit manual Docker forwarding. Container administrators can access mounted files and environments.

Hosted providers do not preload models or need Ollama running. `keep_alive`, `endpoint`, `context_window_size`, and `KIO1_OLLAMA_ENDPOINT` only affect Ollama. Shared `temperature`, `request_timeout`, and `max_output_tokens` still apply. Groq uses `https://api.groq.com/openai/v1/chat/completions`; OpenRouter uses `https://openrouter.ai/api/v1/chat/completions`. Requests use bearer authentication, non-streaming chat, `max_tokens`, and `response_format: {"type": "json_object"}`. Redirects, refusals, empty/malformed responses, responses larger than 1 MiB, and truncated completions fail rather than being persisted as successful turns. HTTP failures (including 401, 429, and 5xx) and timeouts are not retried.

Hosted generation sends the system prompt and full active conversation to the selected external provider. Check data-handling requirements and billing before using sensitive inputs. Missing credentials fail at startup; authentication and model availability are checked on the first actual request, not by a paid startup probe. Direct keys are held privately in `Config`; environment fallback keys are read when needed. Neither source is logged by the provider transport. See [Observability](observability.md) for diagnostics.

## Keeping Model Loaded

Use `keep_alive` to reduce cold starts after launch.

- `-1`: keep model loaded indefinitely (until Ollama restart or resource eviction)
- `N > 0`: keep model loaded for `N` seconds between requests

## Changing the Model

To use a different Ollama model:

1. Pull the model: `ollama pull <model-name>`
2. Update `config.json`:
   ```json
   "model": "<model-name>"
   ```

The `KIO1_OLLAMA_ENDPOINT` environment variable overrides `endpoint` at runtime, which is how the Docker image points at the host's Ollama install without a second config file.

## Context Window

`context_window_size` sets the total token budget for a request — the system prompt, the whole conversation so far, and the generated response all share it. It maps to Ollama's `num_ctx` option. Because every turn re-sends the full transcript, this is what limits how long a conversation can run.

`max_output_tokens` caps the generated response and maps to Ollama's `num_predict`. It must be smaller than `context_window_size` to leave room for the prompt.

Ollama's own default is 4096 regardless of what the model supports (`ministral-3:8b` supports 262144), which is why an explicit value is required. Raising it costs RAM, since the cache is allocated when the model loads — 16384 comfortably fits roughly twenty turns.

Zero and negative values are not sentinels for "use the model maximum". Ollama clamps them to a roughly 4-token window and still returns HTTP 200, producing unrelated output from a prompt that was silently discarded, so they are rejected at startup instead.

## Telemetry

The `telemetry` object is optional. When it is omitted or `telemetry.enabled` is `false`, the application does not initialize OpenTelemetry exporters and can run without the local observability stack.

When enabled, `otlp_http_endpoint` is treated as the OTLP/HTTP base URL. The application automatically sends traces to `/v1/traces` and metrics to `/v1/metrics`.

`trace_sample_ratio` controls trace sampling:

- `1.0`: record every trace
- `0.5`: record approximately half of traces
- `0.0`: record no traces

Trace sampling does not disable metrics.

If the OTLP Collector requires authentication, set `telemetry.otlp_bearer_token` in `config.json`. When set, it is sent as `Authorization: Bearer <token>` on both the trace and metric exporters. When left empty (the default), requests are sent without an `Authorization` header; if the Collector requires one, exports fail with a timeout/retry error rather than a clear auth error — see [troubleshooting.md](troubleshooting.md).

`metric_export_interval_ms` controls how frequently metrics are sent. Smaller values update Prometheus more frequently but increase export activity.

For setup, querying, retention, and safe shutdown instructions, see the [Observability Guide](observability.md).

## Security

Do not put secrets such as `otlp_bearer_token` in source files or committed shell scripts; keep them only in `config.json`, which is gitignored.

## Custom System Prompts

Create a new file under `prompts/` and update `prompt_path` in `config.json`:

```json
"prompt_path": "prompts/my_custom_prompt.txt"
```

The prompt file is plain text. It is sent as the `system` role message to the model on every request.

