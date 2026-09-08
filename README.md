# KIO1: Orchestrator

A terminal application that turns software-development requests into structured JSON workflow plans.

KIO1 supports multiple model providers:

- Ollama
- OpenAI
- Anthropic

Providers are loaded dynamically from `<provider>_client.py`, keeping the main application and configuration loader provider-neutral.

## Requirements

- Python 3.10 or newer
- An Ollama installation, OpenAI API key, or Anthropic API key
- Access to the model configured in `config.json`

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

The active provider and model are selected in `config.json`. API keys are read from environment variables, never from `config.json` itself. See [Configuration](docs/configuration.md) for the full field reference and per-provider setup.

## Usage

```bash
python3 main.py
```

See [Usage Guide](docs/usage.md) for example sessions and sample requests.

## Running Tests

```bash
pytest -q
```

See [Development Guide](docs/development.md) for the full test, lint, and format workflow.

## KIO Agents

KIO1 sends plan steps to the KIO agents listed below. Only agents with an agreed message contract are integrated; steps addressed to any other agent are reported as skipped. See [Dispatch](docs/dispatch.md) for the message flow.

| Agent | Responsibility | Repository | Integration |
|-------|----------------|------------|-------------|
| KIO10 | TinyML and energy-efficiency analysis for resource-constrained devices (IoT, Edge AI, embedded systems) | [ai4sweng/AI4SWENG-KIO10](https://github.com/ai4sweng/AI4SWENG-KIO10) | Contract implemented in `kio10/`; exercised against the in-memory stub until the agent's HTTP endpoint is deployed |

## Documentation

- [Architecture](docs/architecture.md) — System overview, components, and data flow
- [Configuration](docs/configuration.md) — All config.json settings explained
- [Dispatch](docs/dispatch.md) — Sending plan steps to KIO agents, dependencies, KIO10 contract, stub agent
- [Usage Guide](docs/usage.md) — Interactive session examples and workflows
- [API Reference](docs/api-reference.md) — Module and function documentation
- [Chat History Format](docs/chat-history.md) — JSONL session file specification
- [Development Guide](docs/development.md) — Setup, testing, and contribution guidelines
- [Troubleshooting](docs/troubleshooting.md) — Common errors and how to resolve them