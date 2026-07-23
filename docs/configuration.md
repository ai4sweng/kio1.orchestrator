# Configuration

All application settings are stored in `config.json` at the project root.

## Configuration File Reference

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | Provider module name: `ollama`, `openai`, or `anthropic` |
| `allowed_providers` | array of strings | Providers permitted to load; `provider` must be one of these |
| `model` | string | Model identifier used by the selected provider |
| `prompt_path` | string | Path to the system prompt file (relative to project root) |
| `chat_directory` | string | Directory for chat history files |
| `temperature` | float | Sampling temperature (lower = more deterministic) |
| `request_timeout` | float | Request timeout in seconds |
| `max_tokens` | int | Maximum number of generated output tokens; does not limit input context |
| `provider_options` | object | Optional provider-specific configuration (e.g. Ollama's `endpoint`) |

## Ollama

## Example `config.json`

```json
{
    "provider": "ollama",
    "allowed_providers": ["ollama", "openai", "anthropic"],
    "model": "ministral-3:8b",
    "prompt_path": "prompts/kio1_system_prompt_ver1.txt",
    "chat_directory": "chats",
    "temperature": 0.1,
    "request_timeout": 120,
        "provider_options": {
        "endpoint": "http://localhost:11434"
    }
}
```

### Changing the Model

To use a different Ollama model:

1. Pull the model: `ollama pull <model-name>`
2. Update `config.json`:
   ```json
   "model": "<model-name>"
   ```

## OpenAI

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
    "allowed_providers": ["ollama", "openai", "anthropic"],
    "model": "gpt-4o-mini",
    "prompt_path": "prompts/kio1_system_prompt_ver1.txt",
    "chat_directory": "chats",
    "temperature": 0.1,
    "request_timeout": 120,
}
```

The OpenAI provider uses the Chat Completions API and requests JSON output.

## Anthropic

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
    "allowed_providers": ["ollama", "openai", "anthropic"],
    "model": "claude-haiku-4-5",
    "prompt_path": "prompts/kio1_system_prompt_ver1.txt",
    "chat_directory": "chats",
    "temperature": 0.1,
    "request_timeout": 120,
    "max_tokens": 2048
}
```

The formatter accepts both plain JSON and JSON wrapped in a Markdown code fence.

## Security

Do not put API keys in `config.json`, source files, or committed shell scripts. The OpenAI and Anthropic SDKs automatically read the `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` environment variables. Variables set with the commands above last only until the terminal session is closed.


## Custom System Prompts

Create a new file under `prompts/` and update `prompt_path` in `config.json`:

```json
"prompt_path": "prompts/my_custom_prompt.txt"
```

The prompt file is plain text. It is sent as the `system` role message to the model on every request.
