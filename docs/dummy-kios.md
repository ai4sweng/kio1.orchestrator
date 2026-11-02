# Dummy KIO2-KIO13 Containers

One dependency-free image runs as twelve independent agents. Every receiver prints only the incoming request body followed by a line of 72 hyphens. Startup, HTTP access, and health-check messages are suppressed. Each task gets a correlated dummy success response; no real task instructions are executed.

Run the commands below from the repository root.

## Start All Dummy Agents

If the old standalone `kio3` container is still running, stop it first:

```bash
docker stop kio3
```

Do not run the old and new KIO3 receivers on the same network: both would have the DNS name `kio3`, making routing ambiguous. The old `dummy_kio3/` files remain available for compatibility but are not used by this stack.

Create the shared network if it does not exist, build the shared image, and start all twelve services:

```bash
docker network inspect kio1-network >/dev/null 2>&1 || docker network create kio1-network
docker compose -f dummy_kios/compose.yaml build
docker compose -f dummy_kios/compose.yaml up -d --wait
```

Only `kio2` declares the build; the other services use that same image with their own `KIO_AGENT_ID`. No agent ports are published to the host. Check readiness:

```bash
docker compose -f dummy_kios/compose.yaml ps
```

Follow all received messages without Compose prefixes, colors, or timestamps:

```bash
docker compose -f dummy_kios/compose.yaml logs -f --no-log-prefix --no-color
```

To watch one agent only, append its service name, for example `kio3`. An empty log before a task arrives is expected, even while health checks are running.

## AWS: Keep Containers Running

Copy the repository, including `dummy_kios/`, onto the EC2 instance. Docker and the Docker Compose plugin must be installed. From the repository root, enable Docker at boot on a systemd-based EC2 host (Ubuntu/Amazon Linux):

```bash
sudo systemctl enable --now docker
```

Then build and start the same stack on AWS:

```bash
docker network inspect kio1-network >/dev/null 2>&1 || docker network create kio1-network
docker compose -f dummy_kios/compose.yaml build
docker compose -f dummy_kios/compose.yaml up -d --wait
```

You can log out after this finishes. `-d` detaches the containers from SSH, and the shared `restart: unless-stopped` policy applies to all twelve services. They restart after a process exit or Docker/host restart, provided Docker starts and the containers were not explicitly stopped. A health status of `unhealthy` alone does not trigger a restart. No separate daemon, `nohup`, or terminal multiplexer is required.

If the agents are already running from an older Compose file, apply the new restart policy with `up -d --wait` again; a receiver-image rebuild is not needed for this policy-only change. Existing containers may be recreated. The stack does not execute tasks until KIO1 dispatches them.

After reconnecting to SSH, inspect state and follow received bodies:

```bash
docker compose -f dummy_kios/compose.yaml ps
docker compose -f dummy_kios/compose.yaml logs -f --no-log-prefix --no-color
```

Ctrl+C stops only the log viewer, not the detached receivers. To keep them running, do not run `stop` or `down` when logging out. A deliberate `stop` requires `start`/`up` to resume; `down` removes the containers.

KIO1 must also run detached with `--restart unless-stopped` and the complete dummy registry. See the [README AWS example](../README.md#launch-the-container) or the full KIO1 command. Keep Ollama running too. Do not combine `--rm` with `--restart`. No inbound EC2 security-group rules are needed for dummy ports; they communicate only on the Docker network. Protect access to KIO1's UI.

SSH logout is not an EC2 stop or termination: containers cannot run while the instance is stopped. Keep the instance and its storage available; use the normal EC2 lifecycle and storage protections for persistence.

## Connect KIO1

Stop your currently running KIO1 container before recreating it with the complete test registry. Keep the existing chat/log/asset volumes. Use `dummy_kios/agents.dummy.json`, not the old KIO3-only registry.

The following complete example uses Ollama on the host:

```bash
mkdir -p chats logs assets
docker run --rm --network kio1-network \
  -p 5000:5000 \
  --add-host=host.docker.internal:host-gateway \
  -v "$PWD/config.json:/app/config.json:ro" \
  -v "$PWD/chats:/app/chats" \
  -v "$PWD/logs:/app/logs" \
  -v "$PWD/assets:/app/assets" \
  -v "$PWD/dummy_kios/agents.dummy.json:/app/agents.dummy.json:ro" \
  -e KIO1_OLLAMA_ENDPOINT=http://host.docker.internal:11434 \
  -e KIO1_AGENT_CONFIG=/app/agents.dummy.json \
  kio1
```

This assumes your KIO1 image is already built. If Ollama runs in a container named `ollama` on the same network, use `-e KIO1_OLLAMA_ENDPOINT=http://ollama:11434` instead. Mounting the registry does not require changing the KIO1 image.

Generate a workflow in the UI, then click **Run workflow** and confirm. The full registry configures all KIO2-KIO13 endpoints, so multi-agent plans can run without the previous missing-endpoint error. Only agents selected by that plan receive tasks; generating the plan alone still sends nothing.

Each selected agent's log will look like:

```text
{"schema_version": "1.0", "workflow_id": "wf-example", "step_id": "s1", "agent_id": "KIO3", "task": "..."}
------------------------------------------------------------------------
```

The body above is shortened for illustration; actual logs contain the complete, unchanged request including input data, dependency results, and correlation fields. In the UI, successful step results include `dummy: true`, `received: true`, and the receiving `agent_id`. These confirm delivery only; downstream agents receive dummy acknowledgements, not real generated artifacts.

## Stop and Rebuild

Stop and remove the dummy containers without touching KIO1 or the external shared network:

```bash
docker compose -f dummy_kios/compose.yaml down
```

After changing receiver code, rebuild with `build`, then run `up -d --wait` again. Container logs remain until containers are removed; add `--tail 0` to the logs command to watch only new messages.

For an isolated test network, create it yourself and set `KIO_DUMMY_NETWORK` to its name for all Compose commands. KIO1 must join that same network.

## Test-Only Limits

Use synthetic data: full bodies are printed, including bounded malformed JSON. The receivers enforce size/content-type/correlation/agent/header checks but do not implement authentication, durable idempotency, capability/data validation, deadline enforcement, or actual task execution. Repeated tasks are printed again. Do not expose these receivers publicly or connect them to production or flight operations. Real agents must implement the [production contract](workflow-contract.md).
