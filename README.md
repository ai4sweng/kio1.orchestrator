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

## Code Layout

- `main.py` composes application startup and remains the command-line entry point.
- `ai/` contains the Ollama client; `components/` contains application services such as chat-turn orchestration.
- `ui/` contains Flask routes. Templates and static assets remain in the root-level `templates/` and `static/` folders.
- `workflow/` contains plan validation and task dispatch.
- Root-level configuration, chat history, prompt, formatting, and observability modules provide shared support.

The legacy `webapp` and `workflow_planner` imports remain available as compatibility aliases.

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

Before running, click any step's task text to edit it and use its capability checkboxes to select or deselect capabilities for that KIO. Each step needs a nonblank task and at least one capability. **Run workflow** sends your current edits, not the original LLM task. The payload includes the selected `capabilities` list and a compatible primary `capability`. Edits are local to the displayed plan; reloading or switching conversations restores the original saved LLM response.

For Docker, additionally mount `agents.json` read-only, set `KIO1_AGENT_CONFIG=/app/agents.json`, and put KIO1 and the agents on the same Docker network. Pass bearer tokens through environment variables, not JSON files. Do not expose the shared operator UI without an authenticated reverse proxy.

### Replacing Agent Capabilities

Edit [capabilities.json](capabilities.json), or set `KIO1_CAPABILITIES_PATH` to a replacement JSON file. Each agent entry contains a `description` and a nonempty list of unique `capabilities`. This catalog supplies both the system prompt and planner validation; no Python capability map needs editing. Custom prompt files should include `{{KIO_CAPABILITIES}}` where the catalog should appear.

Restart KIO1 after changing the catalog or its path so the startup-loaded prompt and validation use the same definitions. Previously saved plans with retired capabilities are rejected. See [Configuration](docs/configuration.md).

## Docker Deployment: KIO1 Only

These instructions run KIO1 against your existing Ollama installation. No other KIO agents or agent registry are required. Chat, uploads, and plan generation work; task dispatch remains disabled unless you explicitly configure real agents as described above.

Run all build and launch commands from the repository root containing `Dockerfile` and `config.json`, not from `docs/`. Docker Engine must be installed and accessible to your user. A Python virtual environment is not required for Docker builds.

### Local Deployment: Foreground

1. Start Ollama on your machine and download the exact model named in `config.json` using `ollama pull MODEL_NAME`, replacing `MODEL_NAME` with that name. Check its API:

```bash
curl --fail --max-time 5 http://127.0.0.1:11434/api/tags
```

2. Prepare persistent directories and build KIO1:

```bash
make prepare kio1-build
```

The image runs as UID 1000. These host directories must be writable by that UID and `config.json` must be readable. Create directories before mounting them so Docker does not create them as root. Resolve ownership issues on only these intended paths; do not use `chmod 777` or delete data.

3. Verify Ollama from Docker. The default uses host-based Ollama; replace `OLLAMA_URL` with your existing working remote base URL if applicable, without a trailing slash:

```bash
export OLLAMA_URL=http://host.docker.internal:11434
make ollama-check OLLAMA_URL="$OLLAMA_URL"
```

Continue only if it prints `200`. If the host check succeeds but this fails, see [Ollama connectivity](#ollama-connectivity).

4. Launch KIO1 in the same terminal, using the verified `OLLAMA_URL`:

```bash
make kio1-only-run OLLAMA_URL="$OLLAMA_URL"
```

This target uses the default Docker network and does not configure an agent registry. Keep the terminal open. Wait for model loading and `KIO1 Orchestrator web UI` in the output, then open **http://127.0.0.1:5000**. Press Ctrl+C to stop and remove the container; chat history, logs, and uploads remain in the mounted directories.

### AWS Deployment: Background

Copy the repository and your configuration to the EC2 Linux instance. Install Docker Engine and run Ollama there as a persistent service with the configured model, or retain your working remote Ollama service. Run the build and launch commands on EC2 from the repository root.

1. On a systemd-based host such as Ubuntu or Amazon Linux, run this privileged command yourself to enable Docker at boot:

```bash
sudo systemctl enable --now docker
```

2. Prepare storage and build KIO1:

```bash
make prepare kio1-build
```

The same UID 1000 directory-permission requirements apply on EC2. No Docker Compose stack or agent registry is needed for this deployment.

3. Verify Ollama from Docker before replacing an existing working deployment:

```bash
export OLLAMA_URL=http://host.docker.internal:11434
make ollama-check OLLAMA_URL="$OLLAMA_URL"
```

Use your verified remote URL instead if applicable. Continue only if this prints `200`.

4. Launch detached KIO1. If `kio1` already exists, first follow [Rebuild and replace](#rebuild-and-replace), preserving its data:

```bash
make kio1-only-up OLLAMA_URL="$OLLAMA_URL"
```

This target runs independently of SSH and uses `--restart unless-stopped`, allowing recovery after process exits and Docker/host restarts, provided Docker starts and you did not deliberately stop the container.

5. Check startup on EC2:

```bash
make kio1-logs
```

Wait for model loading and the UI startup message, then press Ctrl+C to leave the log viewer. The detached container keeps running. Check the UI on EC2:

```bash
curl --fail --max-time 5 http://127.0.0.1:5000/
```

6. From your laptop, open an SSH tunnel, replacing the SSH login details and adding your usual key option if necessary:

```bash
ssh -N -L 5000:127.0.0.1:5000 ubuntu@EC2_PUBLIC_IP
```

Open **http://127.0.0.1:5000** on your laptop. Amazon Linux commonly uses `ec2-user`. If laptop port 5000 is occupied, forward local port 5001 instead and open that port. Closing the tunnel removes browser access but does not stop KIO1. An existing authenticated host reverse proxy can also reach the loopback-published UI. No public security-group rule for port 5000 is needed; restrict SSH to trusted clients and do not expose Ollama port 11434 publicly.

You can close all SSH terminals after startup. Ollama must also remain running as a host service or detached container with its own restart policy. No `nohup` or terminal multiplexer is needed. Logging out is not stopping or terminating EC2: containers cannot run while the instance is stopped, and persistent files depend on retaining its storage.

After reconnecting, inspect KIO1 and follow logs:

```bash
make kio1-status
make kio1-logs
```

Ctrl+C stops only the log viewer. Deliberately stop with `make kio1-stop` and resume with `make kio1-start`. Do not stop it just to log out. An `unhealthy` status alone does not cause an automatic restart.

### Ollama Connectivity

`KIO1_OLLAMA_ENDPOINT` overrides the endpoint in `config.json`. The Docker examples use `host.docker.internal` with Linux's `host-gateway` mapping. Inside KIO1, `127.0.0.1` refers to KIO1 itself, not the host.

If host `/api/tags` works but the Docker check fails, inspect the listener:

```bash
ss -ltnp 'sport = :11434'
```

A listener bound only to host `127.0.0.1` cannot serve the Docker host gateway. Before widening the listener, configure the host firewall to allow port 11434 only from the intended trusted Docker networks, and keep it closed publicly in the EC2 security group. Firewall commands depend on your Linux distribution and existing rules; do not disable the firewall.

For Ollama installed as a systemd service, run `sudo systemctl edit ollama` yourself and add this override:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

Apply it only after the access restrictions are in place:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

If you run Ollama manually, set the same `OLLAMA_HOST` value in its environment before starting `ollama serve`; do not start a second server on an occupied port. Repeat the host and Docker connectivity checks. If Ollama is already containerized, use its actual DNS name and the shared Docker network for both targets, for example `make ollama-check OLLAMA_URL=http://ollama:11434 KIO1_NETWORK=your-network` and `make kio1-only-run OLLAMA_URL=http://ollama:11434 KIO1_NETWORK=your-network`. Do not assume a container called `ollama` exists.

KIO1 loads its model before starting Flask. A bad endpoint or missing model can prevent the UI from starting and cause nginx 502. Inspect `make kio1-logs` first; if EC2's local UI check succeeds but the public URL fails, inspect nginx's upstream separately. Slow model loading may temporarily make the container unhealthy.

### Rebuild and Replace

Rebuild after changes to application source, UI files, `requirements.txt`, or `Dockerfile`:

```bash
make kio1-build
```

Building does not update existing containers. After the new image's Ollama check succeeds, stop and preserve an existing named container:

```bash
make kio1-stop
docker rename kio1 "kio1-previous-$(date +%Y%m%d-%H%M%S)"
```

Skip the rename for a foreground container created with `--rm`; it is already removed when stopped. Substitute the actual name if different. Relaunch locally with `make kio1-only-run OLLAMA_URL="$OLLAMA_URL"` or on AWS with `make kio1-only-up OLLAMA_URL="$OLLAMA_URL"`. These targets preserve the data mounts. `docker restart kio1` alone still runs the old image. Do not delete mounted data or prune unrelated containers. If port 5000 remains occupied, identify the other listener before stopping any unrelated process.

## Observability

Run `python3 main.py`; startup prints the exact **Session log** path under `logs/`. Follow the newest log while submitting queries:

```bash
tail -f "$(ls -t logs/log_*.log | head -n 1)"
```

Each query logs its duration and generated plan's workflow ID, mode, step count, and agent assignments. Generation reports `dispatch=not_started`; **Run workflow** adds execution timing, per-task outcomes, and succeeded/failed/unknown/blocked counts. Logging works even when OpenTelemetry is disabled. See [Debugging Logs](docs/observability.md#local-debugging-logs) for examples and fields. The new summaries omit payloads; existing DEBUG logs can contain conversation data.

The terminal prints the user query, raw LLM reply, and formatted JSON in that order. **Run workflow** additionally prints each actual agent task payload and writes its exact JSON to the session log immediately before its HTTP request, identified by `Task payload before dispatch`. This includes operator edits, selected capabilities, inputs, and dependency results. Authentication headers are excluded, but task data can be sensitive; protect these logs and redact them before sharing.

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
