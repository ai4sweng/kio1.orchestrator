# API Reference

```
config_loader --> Config (dataclass)
               --> load_config()

prompt_loader --> load_prompt()

ollama_client --> preload_model()
              --> send_request()
              --> extract_content()

chat_history  --> create_chat_file()
              --> append_user_message()
              --> append_assistant_message()
              --> load_messages()

formatter     --> format_json()
```

## `config_loader`

### `Config` (dataclass)

| Field | Type | Description |
|-------|------|-------------|
| `model` | `str` | Ollama model name |
| `ollama_endpoint` | `str` | Ollama API base URL |
| `prompt_path` | `str` | Path to system prompt file |
| `chat_directory` | `str` | Chat history directory |
| `temperature` | `float` | Sampling temperature |
| `request_timeout` | `int` | HTTP timeout in seconds |
| `keep_alive` | `int` | Ollama residency control (`-1` keeps loaded) |

### `load_config(config_path="config.json") -> Config`

Loads a `Config` from a JSON file.

Raises `FileNotFoundError` if the file does not exist. Raises `KeyError` if a required field is missing.

## `prompt_loader`

### `load_prompt(prompt_path) -> str`

Reads and returns the stripped text content of a prompt file.

Raises `FileNotFoundError` if the file does not exist.

## `ollama_client`

### `preload_model(endpoint, model, timeout=120, keep_alive=-1) -> None`

Sends an empty request to Ollama to load the model into memory with configured `keep_alive`.

### `send_request(endpoint, model, system_prompt, messages, temperature=0.1, timeout=120, keep_alive=-1) -> dict`

Sends a chat completion request to Ollama. Returns the parsed JSON response.

| Param | Type | Description |
|-------|------|-------------|
| `endpoint` | `str` | Ollama base URL |
| `model` | `str` | Model identifier |
| `system_prompt` | `str` | System prompt text |
| `messages` | `list[dict]` | Conversation messages with `role` and `content` |
| `temperature` | `float` | Sampling temperature |
| `timeout` | `int` | Request timeout in seconds |
| `keep_alive` | `int` | Ollama residency control value |

### `extract_content(response) -> str`

Extracts the assistant message content from an Ollama response dict.

## `chat_history`

### `create_chat_file(chat_directory, system_prompt) -> Path`

Creates a JSONL chat file with the system prompt as the first line.
Filename format: `chat_<YYYYMMDD_HHMMSS>_<8-char-uuid>.jsonl`

### `append_user_message(chat_file, message) -> None`

Appends a user message as a JSONL line.

### `append_assistant_message(chat_file, message) -> None`

Appends an assistant message as a JSONL line.

### `load_messages(chat_file) -> list[dict]`

Returns all messages from the chat file, excluding the system prompt.

## `formatter`

### `format_json(raw_json) -> str`

Parses and pretty-prints a JSON string with 2-space indentation. Falls back to `ast.literal_eval` for single-quoted Python dict output from the model.
