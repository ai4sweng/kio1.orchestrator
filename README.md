# KIO1: Orchestrator

A browser-based chat UI that sends a user's query to a local Ollama model and renders the model's structured JSON response, messenger-style. Datasets, model checkpoints, or other files can be uploaded from the UI and are saved under `assets/` (not committed to version control).

## Requirements

- Python 3.10 or newer
- An Ollama installation
- Access to the model configured in `config.json`
- Docker (optional, for containerized runs)

## Installation

```bash
make install
source .venv/bin/activate
```

Or manually:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Further information about the project is available in the docs folder

## Configuration

The model and Ollama endpoint are selected in `config.json`. See [Configuration](docs/configuration.md) for the full field reference.

## Usage

```bash
python3 main.py
```

This preloads the configured model and starts the web UI at `http://127.0.0.1:5000` (override with the `KIO1_WEB_HOST`/`KIO1_WEB_PORT` env vars). Open that address in a browser to chat and upload files.

See [Usage Guide](docs/usage.md) for example sessions and sample requests.

Delete an individual conversation using its trash icon in the history sidebar. Deletion requires confirmation; deleting the active chat opens a new empty chat.

### Workflow Planner

KIO1 can validate plans and send tasks to KIO2-KIO13 containers. Dispatch is disabled by default and always separate from generating a plan.

```bash
cp agents.example.json agents.json
# Edit agents.json with the actual container task endpoints.
export KIO1_AGENT_CONFIG=agents.json
python3 main.py
```

Select **Run workflow** below a plan and confirm. Read the brief [container contract](docs/workflow-contract.md) before implementing receivers. It covers payloads, dependencies, responses, idempotency, deadlines, authentication, and the initial synchronous implementation's limits. KIO7 handles code generation; KIO10 handles TinyML and energy efficiency. Existing chat behavior is unchanged.

For Docker, additionally mount `agents.json` read-only, set `KIO1_AGENT_CONFIG=/app/agents.json`, and put KIO1 and the agents on the same Docker network. Pass bearer tokens through environment variables, not JSON files. Do not expose the shared operator UI without an authenticated reverse proxy.

### Dummy KIOs for Integration Testing

`dummy_kios/` provides separate KIO2-KIO13 receivers using one shared image.
Each prints only the received body followed by a separator line; health checks
and HTTP access messages are silent. Receivers acknowledge tasks without
executing their instructions.

```bash
docker network inspect kio1-network >/dev/null 2>&1 || docker network create kio1-network
docker compose -f dummy_kios/compose.yaml build
docker compose -f dummy_kios/compose.yaml up -d --wait
docker compose -f dummy_kios/compose.yaml logs -f --no-log-prefix --no-color
```

`up -d` runs the agents in the background, so they keep running after SSH logout.
All twelve services use `restart: unless-stopped`, allowing recovery after a
crash or Docker restart. For EC2 boot setup and persistent operation, see
[AWS deployment](docs/dummy-kios.md#aws-keep-containers-running).

Stop the old standalone `kio3` container first if it is still running to avoid
duplicate network names. Recreate KIO1 on the same network with
`dummy_kios/agents.dummy.json` mounted read-only and `KIO1_AGENT_CONFIG` set to
its container path. This replaces the old KIO3-only registry and allows
multi-agent workflows. Generate a plan, then select **Run workflow**.
See [Dummy KIOs Guide](docs/dummy-kios.md) for the full KIO1 launch command,
readiness checks, and cleanup. The older [single-KIO3 guide](docs/dummy-kio3.md)
remains available. Use synthetic data only.

### Replacing Agent Capabilities

Edit [capabilities.json](capabilities.json), or set `KIO1_CAPABILITIES_PATH` to a replacement JSON file. Each agent entry contains a `description` and a nonempty list of unique `capabilities`. This catalog supplies both the system prompt and planner validation; no Python capability map needs editing. Custom prompt files should include `{{KIO_CAPABILITIES}}` where the catalog should appear.

Restart KIO1 after changing the catalog or its path so the startup-loaded prompt and validation use the same definitions. Previously saved plans with retired capabilities are rejected. See [Configuration](docs/configuration.md).

### In Docker

The orchestrator also ships a `Dockerfile`. It reaches Ollama on the host via `host.docker.internal`, so mount your `config.json` at runtime and override the endpoint with an env var — the same `config.json` then works both on the host and in the container. `config.json` is mounted (not baked into the image) so credentials such as `otlp_bearer_token` stay out of image layers, and `KIO1_OLLAMA_ENDPOINT` overrides the file's endpoint at runtime, so there is no second config file to drift. Ollama must be running on the host first.

#### Build the container

Rebuild the image whenever `Dockerfile`, `requirements.txt`, or the application source changes:

```bash
docker build -t kio1 .
```

#### Launch the container

Use this to start the already-built image — no rebuild required. Create the mounted host directories first if they do not already exist; otherwise Docker creates them as `root`, and the container's non-root user will not be able to write to them (this bites `assets/` in particular, since it is only created on first upload):

```bash
mkdir -p chats logs assets
```

```bash
docker run --rm -p 5000:5000 --add-host=host.docker.internal:host-gateway \
  -v "$PWD/config.json:/app/config.json" \
  -v "$PWD/chats:/app/chats" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/assets:/app/assets" \
  -e KIO1_OLLAMA_ENDPOINT=http://host.docker.internal:11434 \
  kio1
```

On AWS, run the following from the repository root. Docker and Docker Compose
must be installed. To enable Docker at boot, run this once on the EC2 host:

```bash
sudo systemctl enable --now docker
```

For testing with all dummy KIOs, create their shared network and start them in
detached mode before starting KIO1:

```bash
docker network inspect kio1-network >/dev/null 2>&1 || docker network create kio1-network
docker compose -f dummy_kios/compose.yaml build
docker compose -f dummy_kios/compose.yaml up -d --wait
mkdir -p chats logs assets
```

Build the KIO1 image:

```bash
docker buildx build --load -t kio1-orchestrator:latest .
```

Start KIO1 in the background with the full dummy registry. This example assumes
an Ollama container named `ollama` on `kio1-network`. Stop/remove any previous
KIO1 container using the same name before replacing it; retain its mounted data.

```bash
docker run -d \
  --name kio1 \
  --restart unless-stopped \
  --network kio1-network \
  -p 5000:5000 \
  -v "$PWD/config.json:/app/config.json:ro" \
  -v "$PWD/chats:/app/chats" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/assets:/app/assets" \
  -v "$PWD/dummy_kios/agents.dummy.json:/app/agents.dummy.json:ro" \
  -e KIO1_OLLAMA_ENDPOINT=http://ollama:11434 \
  -e KIO1_AGENT_CONFIG=/app/agents.dummy.json \
  kio1-orchestrator:latest
```

You can now log out of SSH; KIO1 and the dummy agents stay running. Ollama must
also remain running and, if containerized, have an appropriate restart policy.
Detached mode does not keep a stopped or terminated EC2 instance running.
Do not use `--rm` with `--restart`. Restrict port 5000 in the EC2 security group
to trusted clients or an authenticated reverse proxy; no dummy-agent ports need
to be opened. Replace the dummy registry with real endpoints for real execution.

Open `http://localhost:5000` once the container is running. `docker run` always creates a new container rather than reusing one, so without `--rm` every launch leaves a stopped container behind (visible in `docker ps -a`). `--rm` deletes the container as soon as it exits; mounting `chats/`, `logs/`, and `assets/` keeps session history, logs, and uploaded files on the host regardless, so nothing is lost. To clear out containers created before this flag was added:

```bash
docker container prune
```

Stop Container:
```bash
docker stop <CONTAINER ID>
```

## Observability

Run `python3 main.py`; startup prints the exact **Session log** path under `logs/`. Follow the newest log while submitting queries:

```bash
tail -f "$(ls -t logs/log_*.log | head -n 1)"
```

Each query logs its duration and generated plan's workflow ID, mode, step count, and agent assignments. Generation reports `dispatch=not_started`; **Run workflow** adds execution timing, per-task outcomes, and succeeded/failed/unknown/blocked counts. Logging works even when OpenTelemetry is disabled. See [Debugging Logs](docs/observability.md#local-debugging-logs) for examples and fields. The new summaries omit payloads; existing DEBUG logs can contain conversation data.

The terminal prints the user query, raw LLM reply, and formatted JSON in that order. **Run workflow** additionally prints each actual agent task payload before its HTTP request. These full-content prints exclude authentication headers but can contain sensitive task data; redact them before sharing.

<!-- KIO1 can export OpenTelemetry traces and metrics to a local persistent Tempo and Prometheus stack. Telemetry is disabled by default.

Start the observability services with:

```bash
docker compose -f observability/compose.telemetry.yaml up -d
```

See the [Observability Guide](docs/observability.md) for configuration, querying, retention, and safe-shutdown instructions. -->

## Running Tests

```bash
pytest -q
```

See [Development Guide](docs/development.md) for the full test, lint, and format workflow.

## Documentation

- [Dummy KIOs Guide](docs/dummy-kios.md) - Build and run all twelve quiet test receivers
- [Workflow Container Contract](docs/workflow-contract.md) - Receiver implementation and planner setup
- [Architecture](docs/architecture.md) — System overview, components, and data flow
- [Configuration](docs/configuration.md) — All config.json settings explained
- [Usage Guide](docs/usage.md) — Interactive session examples and workflows
- [API Reference](docs/api-reference.md) — Module and function documentation
- [Chat History Format](docs/chat-history.md) — JSONL session file specification
- [Development Guide](docs/development.md) — Setup, testing, and contribution guidelines
- [Troubleshooting](docs/troubleshooting.md) — Common errors and how to resolve them
- [Observability](docs/observability.md) — Tracing, metrics, storage, querying, and safe shutdown
