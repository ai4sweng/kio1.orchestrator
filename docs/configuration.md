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
| `keep_alive` | int | Ollama model residency in seconds (`-1` = keep loaded) | `-1` |
| `max_tokens` | int | Maximum number of generated output tokens; does not limit input context |
| `provider_options.endpoint` | string | Ollama base URL (required for Ollama) |
| `provider_options.num_ctx` | int | Ollama context window in tokens (required for Ollama); must be a positive integer larger than `max_tokens` |

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
        "keep_alive": -1,
        "provider_options": {
        "endpoint": "http://localhost:11434",
        "num_ctx": 16384
    }
}
```


## Keeping Model Loaded

Use `keep_alive` to reduce cold starts after launch.

- `-1`: keep model loaded indefinitely (until Ollama restart or resource eviction)
- `N > 0`: keep model loaded for `N` seconds between requests

## Changing the Model

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

## Context Window

`provider_options.num_ctx` sets the total token budget for a request — the system prompt, the whole conversation so far, and the generated response all share it. Because every turn re-sends the full transcript, this is what limits how long a conversation can run.

`max_tokens` maps to Ollama's `num_predict` and caps only the output, so it must be smaller than `num_ctx` to leave room for the prompt.

Ollama's own default is 4096 regardless of what the model supports (`ministral-3:8b` supports 262144), which is why an explicit value is required. Raising it costs RAM, since the cache is allocated when the model loads — 16384 comfortably fits roughly twenty turns.

Zero and negative values are not sentinels for "use the model maximum". Ollama clamps them to a roughly 4-token window and still returns HTTP 200, producing unrelated output from a prompt that was silently discarded, so they are rejected at startup instead.

## Security

Do not put API keys in `config.json`, source files, or committed shell scripts. The OpenAI and Anthropic SDKs automatically read the `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` environment variables. Variables set with the commands above last only until the terminal session is closed.


## Custom System Prompts

Create a new file under `prompts/` and update `prompt_path` in `config.json`:

```json
"prompt_path": "prompts/my_custom_prompt.txt"
```

The prompt file is plain text. It is sent as the `system` role message to the model on every request.
