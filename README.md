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
pytest tests/ -v
```

## Project Structure

```
config.json          - Application configuration
main.py              - Entry point
config_loader.py     - Configuration loading
prompt_loader.py     - System prompt loading
ollama_client.py     - Ollama HTTP client
chat_history.py      - Conversation history management
formatter.py         - JSON pretty-printing
prompts/             - System prompt files
chats/               - Conversation history files (auto-created)
tests/               - Unit tests
```
