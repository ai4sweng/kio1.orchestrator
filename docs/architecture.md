# Architecture

## Overview

KIO1 Orchestrator is a terminal application that accepts natural-language user requests and produces structured JSON workflow plans. It supports Ollama, OpenAI, and Anthropic through dynamically loaded provider modules. Optional OpenTelemetry instrumentation exports privacy-safe traces and metrics.

## System Diagram

```mermaid
graph TD
    User[User Terminal] -->|query| Main[main.py]
    Main --> ConfigLoader[config_loader.py]
    Main --> PromptLoader[prompt_loader.py]
    Main --> ChatHistory[chat_history.py]
    Main --> ProviderClient[provider_client.py]
    Main --> Formatter[formatter.py]
    Main --> SessionLogger[session_logger.py]
    Main --> Telemetry[telemetry.py]
    Main -->|dispatch.enabled| Dispatcher[kio10/dispatcher.py]
    Dispatcher --> WorkflowPlan[workflow_plan.py]
    Dispatcher --> KIO10Transport[kio10/transport.py]
    KIO10Transport -->|http://| KIO10[KIO10 agent /jobs]
    KIO10Transport -->|stub://| KIO10Stub[kio10/stub.py]
    Dispatcher -->|write| ReportFiles[logs/dispatch_*.json]
    SessionLogger -->|write| LogFiles[logs/*.log]
    ProviderClient -->|dynamic import| OllamaClient[ollama_client.py]
    ProviderClient -->|dynamic import| OpenAIClient[openai_client.py]
    ProviderClient -->|dynamic import| AnthropicClient[anthropic_client.py]
    OllamaClient -->|HTTP POST| Ollama[Ollama API :11434]
    OpenAIClient -->|HTTPS| OpenAIAPI[OpenAI Chat Completions API]
    AnthropicClient -->|HTTPS| AnthropicAPI[Anthropic Messages API]
    ChatHistory -->|read/write| ChatFiles[chats/*.jsonl]
    ConfigLoader -->|read| ConfigFile[config.json]
    PromptLoader -->|read| PromptFile[prompts/*.txt]
    Formatter --> Telemetry
    OllamaClient --> Telemetry
    OpenAIClient --> Telemetry
    AnthropicClient --> Telemetry
    Telemetry -->|OTLP/HTTP| Collector[OpenTelemetry Collector]
    Collector -->|traces| Tempo[Grafana Tempo]
    Collector -->|metrics| Prometheus[Prometheus]
```

## Components

| Module | Responsibility |
|--------|---------------|
| `main.py` | Entry point; REPL loop, orchestrates all other modules |
| `config_loader.py` | Loads and validates `config.json` into a typed `Config` dataclass |
| `session_logger.py` | Per-session log file setup and duration measurement |
| `provider_client.py` | Dynamically imports `<provider>_client.py` and validates it implements the required provider interface |
| `prompt_loader.py` | Reads system prompt text from disk |
| `ollama_client.py` | Sends HTTP requests to the Ollama `/api/chat` endpoint |
| `openai_client.py` | Sends requests via the OpenAI Chat Completions API |
| `anthropic_client.py` | Sends requests via the Anthropic Messages API |
| `chat_history.py` | Manages JSONL-based conversation persistence |
| `formatter.py` | Pretty-prints JSON responses for terminal display |
| `telemetry.py` | Initializes OpenTelemetry, creates spans and metrics, records metadata, and flushes exporters |
| `observability/` | Defines the persistent Collector, Tempo, and Prometheus services |
| `workflow_plan.py` | Parses a plan into typed steps with explicit `depends_on`; rejects cycles and unknown dependencies |
| `kio10/dispatcher.py` | Runs plan steps as `asyncio` tasks ordered by dependencies, passes artifacts downstream, builds the report |
| `kio10/transport.py` | KIO1 ↔ KIO10 message contract: request builder, submit-and-poll loop, HTTP transport over `httpx2` |
| `kio10/stub.py` | In-memory KIO10 stand-in speaking the same contract, for exercising the chain without real agents |

## Data Flow

1. A session ID is generated, logging is initialized, and configuration is loaded.
2. OpenTelemetry is initialized when `telemetry.enabled` is `true`.
3. Inside the startup trace, the system prompt and configured provider are loaded. Ollama preloads the model using the configured `keep_alive`; OpenAI and Anthropic verify that the configured model exists.
4. A new JSONL chat file is created and the session-started metric is recorded.
5. For each user request, the REPL loads the prior messages, adds the new query, and sends the full conversation to the configured provider inside a turn trace.
6. The provider response is formatted and displayed as an unchanged JSON workflow plan, then the user and assistant messages are appended to the chat file.
7. `workflow_plan.py` validates the plan, including its fields, dependencies, and cycles, and prints a one-line `Plan check` verdict.
8. If `dispatch.enabled` is `true` and validation passed, the dispatcher sends steps to their agents in dependency order, passes artifacts downstream, and prints and stores a report at `logs/dispatch_<session_id>_<workflow_id>.json`. See [Dispatch](dispatch.md).
9. Throughout startup and each turn, telemetry records privacy-safe provider metadata, token usage, workflow structure, durations, traces, and metrics without prompt or response content.
10. On exit, the session-completed metric is recorded and pending telemetry is flushed.

## Design Decisions

- **Standard-library Ollama client**: Ollama requests use `urllib.request`, avoiding an additional provider-specific HTTP dependency.
- **JSONL chat storage**: Each message is a single JSON line, enabling append-only writes and simple streaming reads.
- **Model preloading**: The model is loaded into Ollama memory at startup using configured `keep_alive` to reduce cold-start latency.
- **JSON-forced output**: The Ollama request includes `"format": "json"` to guarantee structured responses from the model.
- **Per-session logging**: Each run writes `logs/log_<session_id>.log` at `DEBUG`, paired with its `chats/chat_<session_id>.jsonl` by a shared id. Only `ERROR` reaches the console, keeping interactive output clean.
- **Dispatch off by default**: the terminal application is unchanged unless `dispatch.enabled` is set. Steps addressed to agents absent from `dispatch.agents` are reported as skipped, not as errors.
- **Dependencies drive concurrency**: each step is an `asyncio` task that awaits the tasks it depends on, so independent steps run together whenever a plan carries `depends_on`. Until the system prompt is taught to emit it, plans run by `execution_mode` alone and `mixed` falls back to `sequential`.
- **Only the KIO10 contract is fixed**: the dispatcher requires nothing beyond the envelope of the KIO1 – KIO10 integration document. Polling reuses the acknowledgement while a job runs, so no new status was introduced.
- **Message content at DEBUG only**: `INFO` records carry metadata (provider, model, durations, token counts); user queries and model responses appear only at `DEBUG`, which never reaches stderr.
- **Optional telemetry**: OpenTelemetry is disabled by default, allowing KIO1 to run without Docker or a Collector.
- **Privacy-aware instrumentation**: Telemetry contains operational metadata but excludes prompts, responses, exception messages, and stack traces.
- **Persistent observability storage**: Collector queues, Tempo traces, and Prometheus metrics use named Docker volumes that survive container recreation.
