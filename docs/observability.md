# Observability

KIO1 uses OpenTelemetry to produce distributed traces and application metrics. The local observability stack stores traces in Grafana Tempo and metrics in Prometheus.

Grafana visualization is not included in this stack. It will be added separately.

Telemetry is disabled by default, so the orchestrator remains usable without Docker or an OpenTelemetry Collector.

## Data Flow

```text
KIO1 Orchestrator
        |
        | OTLP/HTTP
        v
OpenTelemetry Collector
        |
        +-- traces --> Tempo
        |
        +-- metrics -> Prometheus
```

The application sends telemetry to the OpenTelemetry Collector. The Collector batches the data and forwards traces to Tempo and metrics to Prometheus.

## Services

| Service | Purpose | Local address |
|---------|---------|---------------|
| OpenTelemetry Collector | Receives and routes application telemetry | `http://localhost:4318` |
| Collector health check | Reports Collector readiness | `http://localhost:13133` |
| Tempo | Stores and queries traces | `http://localhost:3200` |
| Prometheus | Stores and queries metrics | `http://localhost:9090` |

All published service ports bind to `127.0.0.1` and are not exposed to other machines.

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

Prometheus converts dots to underscores and adds metric-type or unit suffixes.

For example:

```text
kio1.turns
```

becomes:

```text
kio1_turns_total
```

The `kio1.turn.duration` histogram produces:

```text
kio1_turn_duration_seconds_bucket
kio1_turn_duration_seconds_count
kio1_turn_duration_seconds_sum
```

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

The local observability stack additionally requires:

- Docker
- Docker Compose

Grafana is not required for collecting or storing telemetry.

## Starting the Complete Local Environment

### 1. Start Ollama

If Ollama is not already running as a system service, start it in a separate terminal:

```bash
ollama serve
```

Confirm that the configured model is available:

```bash
ollama list
```

### 2. Start the Observability Stack

From the project root, run:

```bash
docker compose -f observability/compose.telemetry.yaml up -d
```

This single command starts:

- Tempo
- Prometheus
- The Collector storage initializer
- The OpenTelemetry Collector

The storage initializer sets the persistent Collector volume ownership and then exits successfully.

### 3. Check Service Status

```bash
docker compose -f observability/compose.telemetry.yaml ps -a
```

The expected state is:

- `collector-storage-init`: `Exited (0)`
- `otel-collector`: `Up`
- `prometheus`: `Up`
- `tempo`: `Up`

The initializer exiting with status `0` is expected and does not indicate a failure.

### 4. Check Service Readiness

```bash
curl -f http://127.0.0.1:13133/
curl -f http://127.0.0.1:3200/ready
curl -f http://127.0.0.1:9090/-/ready
```

Expected responses include:

```text
Server available
ready
Prometheus Server is Ready.
```

### 5. Enable Application Telemetry

Set `telemetry.enabled` to `true` in `config.json`:

```json
"telemetry": {
    "enabled": true,
    "service_name": "kio1-orchestrator",
    "otlp_http_endpoint": "http://localhost:4318",
    "metric_export_interval_ms": 5000,
    "trace_sample_ratio": 1.0
}
```

The configured endpoint is the OTLP/HTTP base URL. The application automatically adds:

```text
/v1/traces
/v1/metrics
```

### 6. Start KIO1

Activate the virtual environment if necessary:

```bash
source .venv/bin/activate
```

Then run:

```bash
python3 main.py
```

The complete environment therefore normally requires two startup commands when Ollama is already running:

```bash
docker compose -f observability/compose.telemetry.yaml up -d
python3 main.py
```

## Viewing Metrics

Open Prometheus in a browser:

```text
http://localhost:9090
```

Enter a PromQL expression in the query field and select **Execute**.

While telemetry is actively exporting, query the current turn counter:

```promql
kio1_turns_total
```

Query successful turns:

```promql
kio1_turns_total{kio1_status="success"}
```

Query failed turns:

```promql
kio1_turns_total{kio1_status="error"}
```

Prometheus instant queries only return recently active series. If telemetry has been disabled or the application has stopped, query a historical window instead:

```promql
sum(last_over_time(kio1_turns_total[24h]))
```

Query successful historical turns:

```promql
sum(
  last_over_time(
    kio1_turns_total{kio1_status="success"}[24h]
  )
)
```

Query token usage grouped by input and output:

```promql
sum by (gen_ai_token_type) (
  last_over_time(gen_ai_client_token_usage_sum[24h])
)
```

Query average turn duration:

```promql
sum(last_over_time(kio1_turn_duration_seconds_sum[24h]))
/
sum(last_over_time(kio1_turn_duration_seconds_count[24h]))
```

Select **Table** for current values or **Graph** for values over time.

## Viewing Traces

Tempo exposes an HTTP API for trace searches and retrieval. Grafana will provide the visual trace viewer in a separate ticket.

Search for KIO1 traces:

```bash
curl -sS -G http://127.0.0.1:3200/api/search \
  --data-urlencode 'q={ resource.service.name = "kio1-orchestrator" }' \
  | python3 -m json.tool
```

The response contains trace summaries and their `traceID` values.

Copy a trace ID and retrieve the complete trace:

```bash
curl -sS http://127.0.0.1:3200/api/traces/TRACE_ID \
  | python3 -m json.tool
```

The returned JSON contains:

- Trace and span IDs
- Parent-child relationships
- Span names
- Start and end times
- Durations
- Resource attributes
- Model and provider metadata
- Token usage
- Workflow metadata
- Error status

## Storage and Retention

| Data | Retention | Docker volume |
|------|-----------|---------------|
| Tempo traces | 7 days | `observability_tempo-data` |
| Prometheus metrics | 30 days | `observability_prometheus-data` |
| Collector retry queue | Until delivered, subject to configured queue and storage limits | `observability_collector-data` |

Tempo stores traces under `/var/tempo` inside its container.

Prometheus stores its time-series database under `/prometheus` inside its container.

The Collector volume stores telemetry that is waiting to be delivered to Tempo or Prometheus. It is a retry queue, not the authoritative long-term storage location.

These files use backend-specific storage formats and are not intended to be opened directly. Use the Tempo API and Prometheus UI or API to inspect their contents.

## Persistence

Named Docker volumes survive:

- Container restarts
- Docker daemon restarts
- Container recreation
- `docker compose down` without `-v`

The services use the following volumes:

```text
observability_tempo-data
observability_prometheus-data
observability_collector-data
```

Named volumes protect against container replacement. They are not backups against host disk failure, Docker volume deletion, or running Compose with the volume-removal option.

## Safe Shutdown

Stop the application before stopping the observability services. This gives OpenTelemetry time to flush pending traces and metrics to the Collector.

### 1. Exit KIO1

At the KIO1 prompt, type:

```text
exit
```

This ends the interactive loop, records the session-completion metric, and shuts down the telemetry providers after flushing pending data.

Pressing `Ctrl+C` while waiting at the KIO1 prompt also follows the normal cleanup path, but typing `exit` is preferred.

### 2. Stop the Observability Stack

From the project root, run:

```bash
docker compose -f observability/compose.telemetry.yaml stop
```

This stops Tempo, Prometheus, and the Collector without deleting their containers or named volumes.

Restart the stack with:

```bash
docker compose -f observability/compose.telemetry.yaml up -d
```

### Removing Containers Without Removing Data

The containers and Compose network can be removed safely with:

```bash
docker compose -f observability/compose.telemetry.yaml down
```

The named data volumes remain available.

Recreate the stack with:

```bash
docker compose -f observability/compose.telemetry.yaml up -d
```

### Commands That Delete Telemetry

Do not run the following command unless permanent deletion of all stored telemetry is intended:

```bash
docker compose -f observability/compose.telemetry.yaml down -v
```

Also avoid manually deleting these volumes:

```text
observability_tempo-data
observability_prometheus-data
observability_collector-data
```

Deleting these volumes cannot be undone unless a separate backup exists.

### 3. Stop Ollama

If Ollama was started manually with `ollama serve`, stop it with `Ctrl+C` after KIO1 and the observability stack have stopped.

Stopping Ollama unloads the running server but does not delete downloaded models.

The recommended shutdown order is:

1. Type `exit` in KIO1.
2. Run `docker compose -f observability/compose.telemetry.yaml stop`.
3. Stop Ollama if it was started manually.

## Automatic Recovery

Tempo, Prometheus, and the Collector use:

```yaml
restart: unless-stopped
```

They restart automatically after Docker restarts unless they were deliberately stopped.

The Collector storage initializer uses:

```yaml
restart: "no"
```

It is expected to run once during stack creation and then exit successfully.

Persistent Collector queues allow queued telemetry to survive a Collector restart and continue delivery after downstream services recover.

## Validating the Configuration

Validate the expanded Compose configuration:

```bash
docker compose -f observability/compose.telemetry.yaml config
```

Validate the Collector configuration:

```bash
docker compose -f observability/compose.telemetry.yaml run --rm --no-deps \
  otel-collector validate \
  --config=/etc/otelcol-contrib/config.yaml
```

Validate the Tempo configuration:

```bash
docker compose -f observability/compose.telemetry.yaml run --rm --no-deps \
  tempo \
  -config.file=/etc/tempo/tempo.yaml \
  -config.verify=true
```

Validate the Prometheus configuration:

```bash
docker compose -f observability/compose.telemetry.yaml run --rm --no-deps \
  --entrypoint /bin/promtool \
  prometheus check config /etc/prometheus/prometheus.yaml
```

The Collector and Tempo validation commands return to the prompt without an error when successful. Successful Prometheus validation prints:

```text
SUCCESS
```

## Logs

Display logs from all observability services:

```bash
docker compose -f observability/compose.telemetry.yaml logs \
  tempo prometheus otel-collector
```

Follow new log messages:

```bash
docker compose -f observability/compose.telemetry.yaml logs -f \
  tempo prometheus otel-collector
```

Display logs for only one service:

```bash
docker compose -f observability/compose.telemetry.yaml logs otel-collector
```

## Troubleshooting

For exporter failures, unavailable services, empty queries, and missing traces, see the [Troubleshooting Guide](troubleshooting.md#observability).
