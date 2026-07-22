# Development Guide

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running

## Setup

```bash
git clone <repo-url>
cd orchestrator
make install
source .venv/bin/activate
```

This creates a `.venv` virtual environment and installs all runtime and development dependencies.

If using Ollama, pull the required model:

```bash
ollama pull ministral-3:8b
```

## Project Structure

```
main.py              - Entry point (REPL loop)
config_loader.py     - Configuration loading
prompt_loader.py     - System prompt file reader
provider_client.py   - Dynamic provider loader and interface
openai_client.py     - OpenAI Chat Completions API provider
anthropic_client.py  - Anthropic Messages API provider
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
make test          # run full test suite
make test-cov      # run with coverage report
```

Or directly:

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

## Code Quality

All tools are installed as part of `make install` and wired to `make` targets:

| Target | Tool(s) | Purpose |
| :--- | :--- | :--- |
| `make format` | black, isort | Auto-format code and imports |
| `make format-check` | black, isort | Verify formatting without changing files |
| `make lint` | ruff | Fast linting and style checks |
| `make typecheck` | mypy | Static type analysis |
| `make check` | ruff + mypy | All static checks combined |
| `make test` | pytest | Run test suite |
| `make test-cov` | pytest-cov | Run tests with coverage report |
| `make ci` | format + ruff + mypy + pytest | Full pipeline — run before pushing |
| `make clean` | — | Remove caches and build artefacts |

Run `make help` to see all targets.

## Dependencies

The project intentionally minimizes external dependencies:

- **Runtime**: `openai >= 1.66.0`, `anthropic >= 0.18.1` (Ollama uses the standard library only — no extra dependency).
- **Testing**: `pytest >= 7.0`, `pytest-cov >= 4.0`.
- **Formatting**: `black >= 24.0`, `isort >= 5.13`.
- **Linting**: `ruff >= 0.4`.
- **Type checking**: `mypy >= 1.10`.

## Code Style

- Type annotations on all function signatures.
- Docstrings in Google style with `Args:` and `Returns:` sections.
- No classes except dataclasses for configuration.
- Run `make format` before committing to apply consistent formatting.
- Run `make ci` before opening a pull request.
