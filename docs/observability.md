# Observability

KIO1 uses OpenTelemetry to produce distributed traces and application metrics. Telemetry is exported over OTLP/HTTP to the shared AI4SWENG observability platform, which stores traces in Tempo, metrics in VictoriaMetrics, and visualizes both in Grafana.

Telemetry is disabled by default, so the orchestrator remains usable without any observability infrastructure or network access to the collector.

This repository does not run its own Tempo/Prometheus/Collector stack. KIO1 connects to the shared AI4SWENG observability platform the same way the other KIO modules do: it is configured with the platform's OTLP endpoint and sends telemetry directly to it over the network.

## Data Flow

```text
KIO1 Orchestrator (this machine)
        |
        | OTLP/HTTP  (telemetry.otlp_http_endpoint)
        v
AI4SWENG Observability Platform (remote)
        |
        +-- traces  --> Tempo
        +-- metrics --> VictoriaMetrics
        |
        v
     Grafana
```

The application sends telemetry directly to the platform's OpenTelemetry Collector over the network. The Collector, Tempo, VictoriaMetrics, and Grafana are owned and operated by the observability team, not by this repository.

## Services

| Service | Purpose | Configured via |
|---------|---------|-----------------|
| AI4SWENG Observability Collector | Receives and routes KIO1's telemetry | `telemetry.otlp_http_endpoint` in `config.json` |

The default endpoint currently points at the shared collector:

```text
http://157.230.17.89:4318
```

Before enabling telemetry, confirm the collector is reachable from this machine:

```bash
curl -v http://157.230.17.89:4318/v1/traces
```

A connection refusal or timeout means the collector is unreachable from this network (firewall, VPN, or the platform not currently running) — check with the observability owner before assuming an application bug. Any HTTP response, including an error status, means the network path is open.

## Collected Traces

The orchestrator produces the following spans:

| Span | Description |
|------|-------------|
| `kio1.startup` | Prompt, provider, model, and session initialization |
| `kio1.provider.preload` | Ollama preload or hosted-provider model validation |
| `kio1.turn` | Complete processing time for one user turn |
| `chat <model>` | Model-provider request and response |
| `kio1.response.format` | Response parsing and workflow extraction |

The spans form parent-child relationships. For example, a `kio1.turn` span contains the model request and response-formatting spans.

Trace attributes include:

- Provider and model
- Session ID
- Turn number
- Request duration
- Maximum output tokens
- Temperature
- Response model
- Response ID, when supplied by the provider
- Finish reason
- Input and output token counts
- Workflow ID
- Workflow execution mode
- Workflow step count
- Error type

Every span and metric also carries the resource attributes `kio.id` and `deployment.environment` (see [Enabling Telemetry](#enabling-telemetry)), so the platform's dashboards can correlate KIO1's telemetry with the rest of the AI4SWENG fleet.

## Collected Metrics

| OpenTelemetry metric | Description |
|----------------------|-------------|
| `kio1.sessions.started` | Sessions that reached the interactive prompt |
| `kio1.sessions.completed` | Sessions that completed after entering the interactive loop |
| `kio1.turns` | User turns processed |
| `kio1.turn.duration` | End-to-end turn duration |
| `kio1.provider.preloads` | Provider preload or validation operations |
| `kio1.provider.preload.duration` | Provider preload or validation duration |
| `gen_ai.client.operation.duration` | Model request duration |
| `gen_ai.client.token.usage` | Input and output token usage |
| `kio1.conversation.messages` | Messages supplied to each model request |
| `kio1.response.truncations` | Responses stopped by an output or context limit |
| `kio1.response.format_fallbacks` | Responses parsed using the Python-literal fallback |
| `kio1.workflow.plans` | Workflow plans successfully parsed |
| `kio1.workflow.step.count` | Steps in parsed workflow plans |

> **Note:** these are the metrics instrumented so far. They are not yet the AI4SWENG platform's contractually required metric set (`kio_request_count`, `kio_request_duration_ms`, `kio_request_error_count`, `kio_llm_token_count`, `kio_llm_cost_usd`, `kio_session_active_count`, `kio_heartbeat`) or the platform's dot-style naming/resource-attribute convention beyond `kio.id`/`deployment.environment`. Aligning to that contract is tracked as follow-up work.

The platform's Collector converts dots to underscores on ingest (`add_metric_suffixes: false`, so `kio1.turns` becomes `kio1_turns` rather than `kio1_turns_total`). Histograms still produce `_bucket`, `_count`, and `_sum` series, since that is structurally required. Exact naming depends on the Collector's exporter configuration, which is owned by the observability platform, not this repository.

## Privacy

Telemetry does not contain:

- User prompts
- System-prompt contents
- Model-response contents
- Chat-history contents
- API keys
- Exception messages
- Exception stack traces

Traces contain selected operational identifiers such as session IDs, response IDs, and workflow IDs.

Metrics use low-cardinality labels such as provider, model, status, token type, execution mode, and error type. Per-session identifiers are not attached to metrics.

Application log files are separate from telemetry. Debug log files can contain request and response content, as documented in the troubleshooting guide.

## Requirements

The application requires Python and a supported model provider.

Enabling telemetry additionally requires network reachability to the shared observability platform's OTLP endpoint. No local Docker or Docker Compose installation is required — the platform is hosted remotely.

## Enabling Telemetry

### 1. Confirm collector reachability

```bash
curl -v http://157.230.17.89:4318/v1/traces
```

See [Services](#services) for how to interpret the result.

### 2. Set `telemetry.enabled` to `true` in `config.json`

```json
"telemetry": {
    "enabled": true,
    "service_name": "kio1-orchestrator",
    "otlp_http_endpoint": "http://157.230.17.89:4318",
    "metric_export_interval_ms": 5000,
    "trace_sample_ratio": 1.0,
    "kio_id": "kio1",
    "deployment_environment": "local"
}
```

`kio_id` identifies this service on the platform's shared dashboards (alongside `kio2-sim`, `kio3`, `kio4`, `kio7`, etc.). `deployment_environment` should be changed from `local` if this instance is not running on a developer machine (for example `staging`).

The configured `otlp_http_endpoint` is the OTLP/HTTP base URL. The application automatically appends:

```text
/v1/traces
/v1/metrics
```

### 3. Start KIO1

```bash
source .venv/bin/activate
python3 main.py
```

No other startup step is required — there is no local stack to bring up.

## Central Stack Operations

Storage retention, dashboards, alerting, and the lifecycle of the Collector/Tempo/VictoriaMetrics/Grafana stack are owned and operated by the AI4SWENG observability platform, not by this repository. This repository is only responsible for producing and exporting correct telemetry.

For Grafana access, dashboard URLs, or platform incidents, contact the observability platform owner rather than looking for local Docker Compose commands — none apply here anymore.

## Troubleshooting

For exporter failures, unavailable collector, and missing traces, see the [Troubleshooting Guide](troubleshooting.md#observability). Some subsections there still describe the previous local-Docker-Compose stack and are pending a follow-up revision to match the remote-platform setup described in this document.
