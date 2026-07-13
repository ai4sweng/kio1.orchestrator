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

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the dependencies:

```bash
python3 -m pip install --upgrade -r requirements.txt
```

## Configuration

The active provider and model are selected in `config.json`.

Common configuration fields:

- `provider`: Provider module name, such as `ollama`, `openai`, or `anthropic`.
- `model`: Model identifier used by the selected provider.
- `prompt_path`: Path to the KIO1 system prompt.
- `chat_directory`: Directory where conversation history is stored.
- `temperature`: Sampling temperature passed to the provider.
- `request_timeout`: Request timeout in seconds.
- `max_tokens`: Maximum output-token limit.
- `provider_options`: Optional provider-specific configuration.

### Ollama

Example configuration:

```json
{
    "provider": "ollama",
    "model": "ministral-3:8b",
    "prompt_path": "prompts/kio1_system_prompt_ver1.txt",
    "chat_directory": "chats",
    "temperature": 0.1,
    "request_timeout": 120,
    "max_tokens": 1024,
    "provider_options": {
        "endpoint": "http://localhost:11434"
    }
}
```

Install Ollama if needed:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Pull the configured model:

```bash
ollama pull ministral-3:8b
```

Ensure Ollama is running:

```bash
ollama serve
```

`provider_options.endpoint` is required when using Ollama.

### OpenAI

Set the OpenAI API key in the terminal:

```bash
read -rsp "OpenAI API key: " OPENAI_API_KEY
echo
export OPENAI_API_KEY
```

Example configuration:

```json
{
    "provider": "openai",
    "model": "gpt-4o-mini",
    "prompt_path": "prompts/kio1_system_prompt_ver1.txt",
    "chat_directory": "chats",
    "temperature": 0.1,
    "request_timeout": 120,
    "max_tokens": 1024
}
```

The OpenAI provider uses the Responses API and requests JSON output.

### Anthropic

Set the Anthropic API key in the terminal:

```bash
read -rsp "Anthropic API key: " ANTHROPIC_API_KEY
echo
export ANTHROPIC_API_KEY
```

Example configuration:

```json
{
    "provider": "anthropic",
    "model": "claude-haiku-4-5",
    "prompt_path": "prompts/kio1_system_prompt_ver1.txt",
    "chat_directory": "chats",
    "temperature": 0.1,
    "request_timeout": 120,
    "max_tokens": 1024
}
```

The formatter accepts both plain JSON and JSON wrapped in a Markdown code fence.

## Security

Do not put API keys in `config.json`, source files, or committed shell scripts.

The OpenAI and Anthropic SDKs automatically read these environment variables:

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
```

The environment variables set using the commands above last until the terminal session is closed.

## Usage

Activate the virtual environment if necessary:

```bash
source .venv/bin/activate
```

Start the orchestrator:

```bash
python3 main.py
```

Enter a software-development request:

```text
Build a Python REST API with tests and deployment.
```

KIO1 returns a structured workflow plan containing:

```json
{
  "workflow_id": "wf-example",
  "execution_mode": "sequential",
  "steps": [],
  "explanation": "Summary of the workflow plan."
}
```

Type `exit` or press `Ctrl+C` to quit.

## Conversation History

Each application session creates a JSONL file under the configured chat directory:

```text
chats/chat_<timestamp>_<identifier>.jsonl
```

The file contains the system prompt followed by successful user and assistant messages.

Example:

```json
{"role": "system", "content": "..."}
{"role": "user", "content": "Build a Python REST API."}
{"role": "assistant", "content": "{\"workflow_id\":\"wf-example\",...}"}
```

## Running Tests

Run the complete test suite:

```bash
pytest -q
```

Run with detailed output:

```bash
pytest tests/ -v
```

Run syntax checks:

```bash
python3 -m py_compile main.py config_loader.py provider_client.py ollama_client.py openai_client.py anthropic_client.py
```

Check the patch for whitespace errors:

```bash
git diff --check
```

## Provider Architecture

Providers are loaded dynamically according to the `provider` value in `config.json`.

For example:

```json
"provider": "openai"
```

loads:

```text
openai_client.py
```

Every provider module implements the same functions:

```python
create_client(config)
preload(config, client)
send_request(config, client, system_prompt, messages)
extract_content(response)
```

To add another provider:

1. Create `<provider>_client.py`.
2. Implement the four provider functions.
3. Add any required SDK dependency to `requirements.txt`.
4. Set `"provider": "<provider>"` in `config.json`.
5. Add mocked provider tests.

No change to `main.py`, `config_loader.py`, or `provider_client.py` should be required.

## Project Structure

```text
config.json          - Application and provider selection
config_loader.py     - Provider-neutral configuration loader
main.py              - Terminal application entry point
provider_client.py   - Dynamic provider loader and interface
ollama_client.py     - Ollama HTTP provider
openai_client.py     - OpenAI Responses API provider
anthropic_client.py  - Anthropic Messages API provider
prompt_loader.py     - System-prompt loader
chat_history.py      - JSONL conversation-history management
formatter.py         - JSON normalization and pretty-printing
requirements.txt     - Python dependencies
prompts/             - KIO1 system prompts
chats/               - Generated conversation histories
tests/               - Automated tests
```

## Troubleshooting

### Provider module not found

If the configuration contains:

```json
"provider": "example"
```

the project must contain:

```text
example_client.py
```

### Ollama endpoint missing

Ollama requires:

```json
"provider_options": {
    "endpoint": "http://localhost:11434"
}
```

### OpenAI authentication error

Confirm the key is set:

```bash
python3 -c 'import os; print("set" if os.getenv("OPENAI_API_KEY") else "missing")'
```

### Anthropic authentication error

Confirm the key is set:

```bash
python3 -c 'import os; print("set" if os.getenv("ANTHROPIC_API_KEY") else "missing")'
```

### Model not found

Confirm that the configured model ID exists and is available to the selected provider account.

### Invalid JSON response

The system prompt asks providers to return JSON only. The formatter also removes optional Markdown JSON fences before parsing provider output.