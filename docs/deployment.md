# Independent Deployment

## Shared Network and Ports

Each project has its own image, dependencies, documentation, test suite, and build context. There is no parent Compose stack. Build and run from each checkout's root. KIO1 is `kio1:5000` internally; UI listens on 8080 internally and publishes `127.0.0.1:5000`; dummies remain `kio2:8000` through `kio13:8000` internally. Only UI is host-published by default.

All projects use `kio1-network`. Configure `KIO1_NETWORK`, `KIO_NETWORK`, and `KIO_DUMMY_NETWORK` to the same name for alternate deployments. The existing dummy registry endpoints require no changes. `KIO1_API_URL` in UI defaults to `http://kio1:5000`.

## Hosted Providers

To use Groq or OpenRouter instead of Ollama, select `provider` and `model` in `config.json` and paste the key into `api_key`. No shell export is required: Docker mounts this local file read-only, and the build context excludes it. See [provider credentials](configuration.md#groq-and-openrouter). Keep the file untracked and restrict access while allowing container UID 1000 to read it. Build the updated image and launch normally; skip Ollama connectivity checks. Restart KIO1 after editing the mounted config. Environment variables remain an optional fallback when `api_key` is empty; recreate containers to change their environment or load a rebuilt image. No keys go into the image or version-controlled files. Only the selected provider needs credentials.

## External Ollama

Verify the model named in your configuration is installed and Ollama remains running:

```bash
curl --fail --max-time 5 http://127.0.0.1:11434/api/tags
make ollama-check OLLAMA_URL=http://host.docker.internal:11434
```

Continue only after the Docker check prints `200`. A host listener bound to `127.0.0.1` cannot serve Docker's host gateway. Before widening it, restrict the host firewall to trusted Docker networks and keep 11434 closed publicly. For systemd Ollama, the administrator can set `Environment="OLLAMA_HOST=0.0.0.0:11434"` in its service override, then reload systemd and restart Ollama. Do not disable the firewall or start a second listener. Retain a verified remote Ollama URL if already working.

For containerized external Ollama, use its actual network DNS name with `OLLAMA_URL=http://ollama:11434` only if that name exists on the shared network. Model preload occurs before API startup; inspect `make logs` before diagnosing UI proxy errors.

## Foreground Terminals

1. Dummy project: `make build run`.
2. KIO1 project: `make build run`. The Makefile creates storage and network and mounts `agents.example.json` by default, enabling dummy-agent dispatch. Set `AGENT_CONFIG=agents.json` only when intentionally selecting a custom trusted registry; use `AGENT_CONFIG=` to disable dispatch.
3. UI project: `make build run`; open `http://127.0.0.1:5000`.

Generate a plan, edit if needed, then approve Run workflow. Generation alone sends no task. Dummy output confirms delivery only. Ctrl+C affects only the foreground project in that terminal. KIO1's mounted chats, logs, and uploads remain intact.

## AWS and Detached Operation

Install Docker/Compose on EC2 and retain all three independent checkouts and persistent KIO1 storage. Enable Docker at boot using your distribution's service administration. Run `make up` in each project after building and checking Ollama. KIO1 uses `agents.example.json` by default; override `AGENT_CONFIG` for a custom trusted registry. All use `unless-stopped`. SSH logout does not stop containers; deliberately stopped containers and unhealthy status alone do not trigger restart.

Access from a laptop with `ssh -N -L 5000:127.0.0.1:5000 LOGIN@EC2_HOST`, then open the local URL. Keep SSH restricted to trusted clients; no public 5000 rule is required. An existing authenticated reverse proxy should point to UI's published loopback port, not KIO1. Ollama must also run persistently. EC2 stop/termination is different from logout.

## Replacement and Rollback

Do not run the old combined KIO1 on host port 5000 alongside the new UI. Inspect existing containers and mounts, back up data, stop the intended old deployment, and rename its named KIO1 container before starting the new API. Skip renaming a foreground `--rm` container after it has been removed. Never remove data or stop unrelated listeners.

Build each changed project and recreate only that project. For a detached container, stop and rename the old instance before starting its replacement. Environment changes such as `AGENT_CONFIG` take effect only when KIO1 is recreated; restarting an old container retains its previous environment. Resume old containers only after freeing the intended name/port/network alias; do not let both versions share the same `kio1` DNS alias. Restoring the archived monorepo implementation requires rebuilding its old image and routing browser traffic directly to its old published port. Merely restarting a container does not load a new image.