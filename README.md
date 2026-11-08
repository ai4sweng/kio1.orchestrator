# KIO1 Orchestrator API

KIO1 owns model requests, prompts and capabilities, chat history, uploads, workflow validation and dispatch, and telemetry. The browser UI and dummy receivers are independent projects. KIO1 builds without either checkout.

## Requirements

Python 3.10+ for local development; Docker for container deployment; a reachable external Ollama service with the model named in [config.json](config.json). Docker examples keep Ollama external and use `http://host.docker.internal:11434` on Linux through the host-gateway mapping.

## Build and Run

Run commands from this project root with Ollama running and reachable:

```bash
make build
make run
```

`make run` automatically creates missing storage directories and the shared Docker network. The container runs as UID 1000; ensure its mounted storage directories are writable by that UID and configuration is readable. Never use `chmod 777`. The default Ollama URL is `http://host.docker.internal:11434`, so no URL argument is needed for the default setup.

This starts the API as `kio1` on `kio1-network`, without publishing a host port. By default, KIO1 mounts `agents.example.json`, enabling the **Run workflow** button for the KIO2-KIO13 dummy receivers. Start the dummy-kios project as well to dispatch tasks; without it, the button appears but dispatch cannot reach its receivers. In separate terminals, run `make build` followed by `make run` from the dummy-kios project and the UI project. Open **http://127.0.0.1:5000**, served by UI. All three run commands create the same network if absent; all launch commands require built images. Startup order is flexible, but wait for KIO1 model preload before using UI. Reload UI if its initial settings request failed.

To disable workflow dispatch, run `make run AGENT_CONFIG=`. Chat, history, uploads, and plan generation remain available through UI without receivers.

### Optional Workflow Dispatch

The bundled dummy registry is used by default. To use a different trusted registry, retain an existing `agents.json` or create one from the example, then launch with its path:

```bash
make run AGENT_CONFIG=agents.json
```

Registry selection is explicit and is never inferred from an LLM response. The bundled [agents.example.json](agents.example.json) matches the dummy service DNS names; adapt trusted endpoints when using real agents. Generating a plan does not execute it; dispatch still requires operator approval in UI.

### Optional Connectivity Check

After building, you can check Ollama connectivity before launching KIO1:

```bash
make ollama-check
```

This is a diagnostic, not a required startup command. For a different Ollama endpoint, pass `OLLAMA_URL` to both the check and launch commands, for example `make run OLLAMA_URL=http://ollama:11434` when that container exists on the shared network.

### Local Python Development

```bash
make install
source .venv/bin/activate
python main.py
```

Local startup binds the API to `127.0.0.1:5000`, not a browser page. Set `KIO1_WEB_HOST` and `KIO1_WEB_PORT` if needed. A Docker UI cannot reach a loopback-only host API; use the all-container workflow unless configuring trusted host access deliberately.

## Configuration and Persistence

Docker mounts [config.json](config.json) read-only and mounts `chats/`, `logs/`, and `assets/` from this project. Existing files stay in place and are not migrated or reset. `AGENT_CONFIG` mounts the selected existing registry read-only. Tokens remain environment-based; use an appropriate Docker environment file for real agents, never store secrets in source or image layers.

`OLLAMA_URL` overrides the model endpoint for Docker commands. `KIO1_NETWORK` overrides the shared network; configure the same name in UI (`KIO_NETWORK`) and dummy-kios (`KIO_DUMMY_NETWORK`). See [Configuration](docs/configuration.md) and [Deployment](docs/deployment.md) for endpoint checks, host binding, and AWS operation.

## Verification

```bash
make install
source .venv/bin/activate
make ci
make test-cov
```

The tests mock Ollama and exporters and use temporary data. Neither UI nor dummy source is needed. `make ci` is non-mutating: format check, lint, typecheck, and unit tests. See [Development](docs/development.md) for implementation guidance.

## Operations and Safety

Use `make up` instead of `make run` for detached operation with `unless-stopped`. Use `make logs`, `make status`, and `make stop`; existing `kio1-*` targets remain aliases. To resume a stopped detached container use `make kio1-start`. Build changes do not update running containers; preserve the old named container before replacement and retain its data mounts.

Health at `/health` reports a started API, not current Ollama or receiver readiness. `/api/settings` returns only model name, workflow-enabled status, and capabilities. Existing API payloads, locks, approval requirements, timeouts, and task contracts are preserved. KIO1 still has one shared operator session, no authentication, no durable workflow queue, and no automatic task recovery. Do not increase worker count: state and locks are process-local.

Do not expose UI or API publicly without authenticated access. Keep Ollama and receivers private. Nginx must never retry workflow requests automatically. Logs can contain queries, model replies, and task data; protect storage. Dummy success means delivery only, never actual work or flight execution.

## Documentation

- [Architecture](docs/architecture.md)
- [API Reference](docs/api-reference.md)
- [Workflow Contract](docs/workflow-contract.md)
- [Observability](docs/observability.md)
- [Chat History](docs/chat-history.md)
- [Migration and Verification Report](docs/project-split.md)

The guide in [archive](docs/archive/monorepo-readme.md) is retained only as historical reference. Its combined deployment commands are obsolete.
