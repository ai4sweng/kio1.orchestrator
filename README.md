# KIO1: Orchestrator

A terminal application that sends user queries to a local Ollama instance and returns structured JSON workflow plans.

## Prerequisites

Install Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Pull the required model:

```bash
ollama pull ministral-3:8b
```

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

## Environment Setup

Activate the virtual environment before running:

```bash
source .venv/bin/activate
```

Ensure the Ollama service is running (`ollama serve` or via systemd).

## Usage

```bash
python main.py
```

Type your request at the prompt. The orchestrator will return a JSON workflow plan. Type `exit` or press `Ctrl+C` to quit.

## Running Tests

```bash
make test          # run test suite
make test-cov      # run tests with coverage report
```

Or directly:

```bash
pytest tests/ -v
```

## Code Quality

```bash
make format        # auto-format with black + isort
make format-check  # verify black + isort formatting
make lint          # ruff linter
make typecheck     # mypy static analysis
make check         # lint + typecheck together
make ci            # full pipeline: format + check + test
```

See `make help` for all available targets.

## Documentation

- [Architecture](docs/architecture.md) — System overview, components, and data flow
- [Configuration](docs/configuration.md) — All config.json settings explained
- [Usage Guide](docs/usage.md) — Interactive session examples and workflows
- [API Reference](docs/api-reference.md) — Module and function documentation
- [Chat History Format](docs/chat-history.md) — JSONL session file specification
- [Development Guide](docs/development.md) — Setup, testing, and contribution guidelines

## Project Structure

```
config.json          - Application configuration
main.py              - Entry point
config_loader.py     - Configuration loading
prompt_loader.py     - System prompt loading
ollama_client.py     - Ollama HTTP client
chat_history.py      - Conversation history management
formatter.py         - JSON pretty-printing
Makefile             - Developer workflow targets
requirements.txt     - Runtime + dev dependencies
prompts/             - System prompt files
chats/               - Conversation history files (auto-created)
tests/               - Unit tests
docs/                - Documentation
```
