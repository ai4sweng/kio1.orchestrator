# Configuration

All application settings are stored in `config.json` at the project root.

## Configuration File Reference

| Field | Type | Description | Default |
|-------|------|-------------|---------|
| `model` | string | Ollama model identifier | `"ministral-3:8b"` |
| `ollama_endpoint` | string | Base URL of the Ollama API | `"http://localhost:11434"` |
| `prompt_path` | string | Path to the system prompt file (relative to project root) | `"prompts/kio1_system_prompt_ver1.txt"` |
| `chat_directory` | string | Directory for chat history files | `"chats"` |
| `temperature` | float | Sampling temperature (lower = more deterministic) | `0.1` |
| `request_timeout` | int | HTTP request timeout in seconds | `120` |

## Example `config.json`

```json
{
    "model": "ministral-3:8b",
    "ollama_endpoint": "http://localhost:11434",
    "prompt_path": "prompts/kio1_system_prompt_ver1.txt",
    "chat_directory": "chats",
    "temperature": 0.1,
    "request_timeout": 120
}
```

## Changing the Model

To use a different Ollama model:

1. Pull the model: `ollama pull <model-name>`
2. Update `config.json`:
   ```json
   "model": "<model-name>"
   ```

## Custom System Prompts

Create a new file under `prompts/` and update `prompt_path` in `config.json`:

```json
"prompt_path": "prompts/my_custom_prompt.txt"
```

The prompt file is plain text. It is sent as the `system` role message to the model on every request.
