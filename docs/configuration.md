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
| `keep_alive` | int | Ollama model residency in seconds (`-1` = keep loaded; default: `-1`) |
| `max_output_tokens` | int | Maximum number of generated output tokens; must be positive (default: `4096`) |
| `provider_options.endpoint` | string | Ollama base URL (required for Ollama) |
| `provider_options.context_window_size` | int | Ollama context window in tokens (required for Ollama); must be larger than `max_output_tokens` |
| `dispatch.enabled` | bool | Send plan steps to KIO agents after printing the plan (default: `false`) |
| `dispatch.poll_interval_seconds` | number | Pause between polls of a running job (default: `2`) |
| `dispatch.step_timeout_seconds` | number | Maximum time for one step from submission to final reply (default: `600`) |
| `dispatch.max_parallel_steps` | int | Maximum number of steps in flight at once, whatever the dependencies allow; must be positive (default: `4`) |
| `dispatch.agents` | object | Agent id to address: `http://`/`https://` endpoint or `stub://` for the in-memory stub; missing agents are treated as not deployed |

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
    "max_output_tokens": 4096,
    "provider_options": {
        "endpoint": "http://localhost:11434",
        "context_window_size": 16384
    },
    "dispatch": {
        "enabled": false,
        "poll_interval_seconds": 2,
        "step_timeout_seconds": 600,
        "max_parallel_steps": 4,
        "agents": {
            "KIO10": "stub://"
        }
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
    "max_output_tokens": 2048
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
    "max_output_tokens": 2048
}
```

The formatter accepts both plain JSON and JSON wrapped in a Markdown code fence.

## Context Window

`provider_options.context_window_size` sets the total token budget for a request — the system prompt, the whole conversation so far, and the generated response all share it. It maps to Ollama's `num_ctx` option. Because every turn re-sends the full transcript, this is what limits how long a conversation can run.

`max_output_tokens` caps the generated response. It maps to Ollama's `num_predict`, OpenAI's `max_completion_tokens`, and Anthropic's `max_tokens`. For Ollama, it must be smaller than `context_window_size` to leave room for the prompt.

Ollama's own default is 4096 regardless of what the model supports (`ministral-3:8b` supports 262144), which is why an explicit value is required. Raising it costs RAM, since the cache is allocated when the model loads — 16384 comfortably fits roughly twenty turns.

Zero and negative values are not sentinels for "use the model maximum". Ollama clamps them to a roughly 4-token window and still returns HTTP 200, producing unrelated output from a prompt that was silently discarded, so they are rejected at startup instead.

## Security

Do not put API keys in `config.json`, source files, or committed shell scripts. The OpenAI and Anthropic SDKs automatically read the `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` environment variables. Variables set with the commands above last only until the terminal session is closed.


## Dispatch

The `dispatch` section is optional and off by default. Set `enabled` to `true` to send each plan step to its agent and print a per-step report. See [Dispatch](dispatch.md) for the message contract, dependency handling and result statuses.

## Custom System Prompts

Create a new file under `prompts/` and update `prompt_path` in `config.json`:

```json
"prompt_path": "prompts/my_custom_prompt.txt"
```

The prompt file is plain text. It is sent as the `system` role message to the model on every request.
