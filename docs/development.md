# Development Guide

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running

## Setup

```bash
git clone <repo-url>
cd orchestrator
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Pull the required model:

```bash
ollama pull ministral-3:8b
```

## Project Structure

```
main.py              - Entry point (REPL loop)
config_loader.py     - Configuration loading
prompt_loader.py     - System prompt file reader
ollama_client.py     - Ollama HTTP client
chat_history.py      - JSONL conversation persistence
formatter.py         - JSON pretty-printing
config.json          - Application settings
prompts/             - System prompt files
chats/               - Session history (auto-created, gitignored)
tests/               - Unit tests
docs/                - Documentation
```

## Running Tests

```bash
pytest tests/ -v
```

All tests use mocks for the Ollama API and temporary directories for file I/O. No running Ollama instance is required for testing.

## Adding a New Module

1. Create the module file at the project root.
2. Import and use it from `main.py`.
3. Add corresponding tests in `tests/test_orchestrator.py`.
4. Update `docs/api-reference.md` with the new functions.

## System Prompt Development

System prompts live in `prompts/`. To iterate on a prompt:

1. Create a new version file (e.g., `kio1_system_prompt_ver2.txt`).
2. Update `prompt_path` in `config.json`.
3. Restart the orchestrator.

Keep previous versions for comparison and rollback.

## Dependencies

The project intentionally minimizes external dependencies:

- **Runtime**: Python standard library only (no `requests`, no `httpx`).
- **Testing**: `pytest >= 7.0`.

## Code Style

- Type annotations on all function signatures.
- Docstrings in Google style with `Args:` and `Returns:` sections.
- No classes except dataclasses for configuration.
