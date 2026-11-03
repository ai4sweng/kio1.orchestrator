# Dummy KIO2-KIO13 Containers

This guide runs KIO1 and all twelve dummy agents on one Docker network. Each dummy prints the complete received JSON body followed by 72 hyphens and returns a correlated success response. It does not execute tasks. Startup, health checks, and HTTP access logs are silent.

Run every command from the repository root, the directory containing `Dockerfile`, `config.json`, and `dummy_kios/`, not from `docs/`.

## Prerequisites

- Docker Engine and the Compose plugin must be installed and accessible to your user. Check `docker version` and `docker compose version`.
- Ollama must be running with the exact model named in `config.json` already downloaded. Check the host with `curl --fail --max-time 5 http://127.0.0.1:11434/api/tags`.
- The examples use host-based Ollama. Its listener must be reachable through Docker's host gateway, not just `127.0.0.1`. See [Ollama connectivity](../README.md#ollama-connectivity). Retain an existing working remote URL if applicable.
- The image runs as UID 1000. Host directories `chats/`, `logs/`, and `assets/` must be writable by that UID; `config.json` must be readable. Create missing directories yourself so Docker does not create them as root. Do not use `chmod 777`.

Only the UI port is published. KIO1 reaches receivers by Docker DNS names such as `http://kio7:8000/tasks`. The full registry maps KIO2-KIO13 to these names. No receiver ports need publishing.

## Local Deployment: Foreground Terminals

Use two terminals in the same repository. Keep both open while testing. These runs are attached, not detached; use Ctrl+C to stop them. Use the AWS instructions when you need operation independent of the terminal.

### 1. Build Both Images

In terminal 1:

```bash
mkdir -p chats logs assets
docker network inspect kio1-network >/dev/null 2>&1 || docker network create kio1-network
docker compose -f dummy_kios/compose.yaml build
docker build -t kio1-orchestrator:latest .
```

Compose builds `kio-dummy:latest`. Only `kio2` declares the build; all twelve services reuse that image with different `KIO_AGENT_ID` values. The second build creates the separate KIO1 image. No Python virtual environment is required for Docker builds.

### 2. Start All Dummy Agents

Keep terminal 1 open:

```bash
docker compose -f dummy_kios/compose.yaml up --no-log-prefix --no-color
```

Do not add `-d` for this foreground setup. Empty receiver logs are normal until tasks arrive. Compose may still print its own container lifecycle messages. Stop an old standalone `kio3` container if it shares this network; its DNS name can conflict with the Compose service.

### 3. Verify Connectivity

In terminal 2, from the same repository root:

```bash
docker compose -f dummy_kios/compose.yaml ps
export OLLAMA_URL=http://host.docker.internal:11434
docker run --rm --network kio1-network \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_URL="$OLLAMA_URL" \
  --entrypoint python kio1-orchestrator:latest \
  -c 'import os, urllib.request; print("Ollama:", urllib.request.urlopen(os.environ["OLLAMA_URL"] + "/api/tags", timeout=5).status); print("KIO7:", urllib.request.urlopen("http://kio7:8000/health", timeout=5).status)'
```

Wait until all twelve services are healthy. The check must print `Ollama: 200` and `KIO7: 200`. If needed, repeat it after startup. For remote Ollama, set `OLLAMA_URL` to its verified base URL without a trailing slash before the check. Do not continue after a failed check; see [Troubleshooting](#troubleshooting).

### 4. Start KIO1 in the Foreground

In terminal 2, with the same `OLLAMA_URL`:

```bash
docker run --rm \
  --name kio1 \
  --network kio1-network \
  --add-host=host.docker.internal:host-gateway \
  -p 127.0.0.1:5000:5000 \
  -v "$PWD/config.json:/app/config.json:ro" \
  -v "$PWD/chats:/app/chats" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/assets:/app/assets" \
  -v "$PWD/dummy_kios/agents.dummy.json:/app/agents.dummy.json:ro" \
  -e KIO1_AGENT_CONFIG=/app/agents.dummy.json \
  -e KIO1_OLLAMA_ENDPOINT="$OLLAMA_URL" \
  kio1-orchestrator:latest
```

Keep terminal 2 open. Wait for model loading and `KIO1 Orchestrator web UI` in the startup output, then open **http://127.0.0.1:5000**. Generate a workflow, edit its task text or capability selections if needed, then click **Run workflow** and confirm. Generation alone sends nothing. Terminal 1 prints the edited JSON received by selected agents. The UI's `dummy: true` and `received: true` confirm delivery, not execution.

Ctrl+C in terminal 2 stops KIO1, and `--rm` removes its container, not mounted files. Ctrl+C in terminal 1 stops the dummy agents. Remove their stopped containers later with:

```bash
docker compose -f dummy_kios/compose.yaml down
```

This leaves the external shared network and KIO1's mounted data intact.

## AWS Deployment: Survives SSH Logout

Use an EC2 Linux instance with Docker Engine, the Compose plugin, the complete repository, and a running Ollama service with the configured model. Run these commands on EC2 from the repository root, not on your laptop.

### 1. Enable Docker and Build

On a systemd-based host such as Ubuntu or Amazon Linux, run this privileged command yourself to enable Docker at boot:

```bash
sudo systemctl enable --now docker
```

Then prepare storage and build both images:

```bash
mkdir -p chats logs assets
docker network inspect kio1-network >/dev/null 2>&1 || docker network create kio1-network
docker compose -f dummy_kios/compose.yaml build
docker build -t kio1-orchestrator:latest .
```

### 2. Start Dummy Agents in the Background

```bash
docker compose -f dummy_kios/compose.yaml up -d --wait
docker compose -f dummy_kios/compose.yaml ps
```

All twelve services must be healthy. `-d` detaches them from SSH; the Compose file already sets `restart: unless-stopped` on every service.

### 3. Verify Ollama and Receiver Connectivity

The default uses Ollama on the EC2 host. Substitute your verified remote base URL if applicable:

```bash
export OLLAMA_URL=http://host.docker.internal:11434
docker run --rm --network kio1-network \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_URL="$OLLAMA_URL" \
  --entrypoint python kio1-orchestrator:latest \
  -c 'import os, urllib.request; print("Ollama:", urllib.request.urlopen(os.environ["OLLAMA_URL"] + "/api/tags", timeout=5).status); print("KIO7:", urllib.request.urlopen("http://kio7:8000/health", timeout=5).status)'
```

Continue only after both checks print `200`. Confirm connectivity before stopping an existing working KIO1 deployment.

### 4. Launch KIO1 in the Background

If a container named `kio1` already exists, follow [Replacing KIO1](#replacing-kio1) first. Retain its configuration and data mounts.

```bash
docker run -d \
  --name kio1 \
  --restart unless-stopped \
  --network kio1-network \
  --add-host=host.docker.internal:host-gateway \
  -p 127.0.0.1:5000:5000 \
  -v "$PWD/config.json:/app/config.json:ro" \
  -v "$PWD/chats:/app/chats" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/assets:/app/assets" \
  -v "$PWD/dummy_kios/agents.dummy.json:/app/agents.dummy.json:ro" \
  -e KIO1_AGENT_CONFIG=/app/agents.dummy.json \
  -e KIO1_OLLAMA_ENDPOINT="$OLLAMA_URL" \
  kio1-orchestrator:latest
```

All Docker options belong before the image name. Do not combine `--rm` with `--restart`.

### 5. Check Startup and Open the UI

On EC2, follow startup:

```bash
docker logs -f kio1
```

Wait for model loading and the UI startup message. Press Ctrl+C to leave the log viewer; the detached container keeps running. Then check the UI on EC2:

```bash
curl --fail --max-time 5 http://127.0.0.1:5000/
```

From a terminal on your laptop, open an SSH tunnel. Replace the login details and add your usual SSH key option if required:

```bash
ssh -N -L 5000:127.0.0.1:5000 ubuntu@EC2_PUBLIC_IP
```

Open **http://127.0.0.1:5000** on your laptop. Amazon Linux commonly uses `ec2-user`. If laptop port 5000 is occupied, use `-L 5001:127.0.0.1:5000` and open port 5001. Closing the tunnel removes browser access but does not stop EC2 containers. An existing authenticated host reverse proxy can also reach this loopback-published UI.

No public port 5000 rule is needed with the SSH tunnel. Restrict SSH to trusted clients. Do not expose ports 8000 or 11434 publicly.

### 6. Log Out and Reconnect

After startup you can close every SSH terminal. KIO1 and the dummy agents remain running. Ollama must also remain running as a host service or a detached container with its own restart policy.

After reconnecting, inspect state:

```bash
docker ps
docker compose -f dummy_kios/compose.yaml ps
```

Watch task bodies:

```bash
docker compose -f dummy_kios/compose.yaml logs -f --no-log-prefix --no-color
```

Add `--tail 0` for only new messages, or append `kio7` to watch that receiver alone. View KIO1 logs with `docker logs -f kio1` in another terminal. Ctrl+C stops only a log viewer.

KIO1 also saves the complete outgoing task JSON in its mounted session log under `logs/log_<session-id>.log`, immediately before each dispatch. Search it with `rg 'Task payload before dispatch' logs/log_*.log`. Each entry identifies the workflow, step, and KIO and includes edited task text, selected capabilities, input data, and dependency results. This records an attempted send, not confirmed receipt. Authentication headers are excluded, but task contents are not redacted; use synthetic data and protect these files. Unlike container logs, these mounted files survive container removal.

Do not run `stop` or `down` when logging out. `unless-stopped` supports recovery after process exits or Docker/host restarts if Docker starts and the containers were not deliberately stopped. An `unhealthy` status alone does not trigger a restart. No `nohup` or terminal multiplexer is required. SSH logout is not an EC2 stop or termination: no containers run while the instance is stopped, and mounted data requires retaining its storage.

## Rebuilds and Deliberate Shutdown

### Replacing KIO1

Rebuild after application, UI, dependency, or Dockerfile changes:

```bash
docker build -t kio1-orchestrator:latest .
```

An image build does not update an existing container. Verify connectivity, then preserve an existing named container before launching its replacement:

```bash
docker stop kio1
docker rename kio1 "kio1-previous-$(date +%Y%m%d-%H%M%S)"
```

Skip the rename for a foreground container started with `--rm`, because stopping removes it automatically. Substitute the actual name if your previous container is not called `kio1`. Run the appropriate local or AWS launch command with the same host data mounts. `docker restart kio1` alone still uses the old image. Do not delete mounted data or prune unrelated containers.

### Rebuilding Receivers

After changing receiver code:

```bash
docker compose -f dummy_kios/compose.yaml build
```

For local use, stop the foreground Compose run and rerun the local `up` command. For AWS:

```bash
docker compose -f dummy_kios/compose.yaml up -d --wait
```

To deliberately stop AWS services without removing containers:

```bash
docker stop kio1
docker compose -f dummy_kios/compose.yaml stop
```

Resume with `docker start kio1` and `docker compose -f dummy_kios/compose.yaml up -d --wait`. Compose `down` removes dummy containers and their container logs but leaves KIO1 and the external network intact.

## Troubleshooting

- **Image not found:** run both builds first. KIO1 uses `kio1-orchestrator:latest`; receivers use `kio-dummy:latest`.
- **Name already in use or port 5000 occupied:** inspect `docker ps -a`. Preserve the container you intend to replace as described above. Another host process may also occupy the port; do not stop unrelated services.
- **Permission denied:** inspect `ls -ld chats logs assets`. Grant UID 1000 access only to these intended directories, preserving their data. Do not make them world-writable.
- **Ollama connection refused:** check host `/api/tags`, then repeat the Docker check. A listener bound only to `127.0.0.1` cannot be reached through the host gateway. See [Ollama connectivity](../README.md#ollama-connectivity).
- **Ollama DNS failure:** use `http://ollama:11434` only if a real Ollama container has the name or alias `ollama` on `kio1-network`. Set `OLLAMA_URL` to its verified DNS URL before the check and launch. For host-based Ollama, use the shown host-gateway mapping.
- **Receiver delivery failure:** check that all containers share `kio1-network`, receivers are healthy, and the full registry mount and `KIO1_AGENT_CONFIG` match the launch command. Do not use the old KIO3-only registry. `/health` checks reachability; actual dispatch requires Run workflow.
- **nginx 502 or unhealthy during startup:** inspect `docker logs kio1`. Model loading happens before Flask starts. Resolve Ollama errors and wait for the UI startup message. If EC2's local UI check succeeds but a public URL fails, inspect nginx's upstream configuration separately.

## Test-Only Limits

Use synthetic data. Full bodies are printed, including bounded malformed JSON. Receivers enforce size, content-type, correlation, agent-ID, and header checks, but not authentication, durable idempotency, capability/data validation, deadline enforcement, or real execution. Repeated tasks are printed again. Downstream steps get dummy acknowledgements, not generated artifacts. Do not expose receivers publicly or connect them to production or flight operations. Real agents must implement the [production contract](workflow-contract.md).
