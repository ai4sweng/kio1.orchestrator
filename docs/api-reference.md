# API Reference

```
config_loader --> Config (dataclass)
               --> load_config()

provider_client --> ProviderModule (Protocol)
                --> load_provider()

prompt_loader --> load_prompt()

ollama_client / openai_client / anthropic_client
                --> create_client(config)
                --> preload(config, client)
                --> send_request(config, client, system_prompt, messages)
                --> extract_content(response)

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
| `provider` | `str` | Provider module name |
| `allowed_providers` | `frozenset[str]` | Providers permitted to load |
| `model` | `str` | Model identifier |
| `prompt_path` | `str` | Path to system prompt file |
| `chat_directory` | `str` | Chat history directory |
| `temperature` | `float` | Sampling temperature |
| `request_timeout` | `float` | HTTP timeout in seconds |
| `max_tokens` | `int` | Maximum output tokens |
| `provider_options` | `dict[str, Any]` | Provider-specific configuration |


### `load_config(config_path="config.json") -> Config`

Loads a `Config` from a JSON file. Validates `provider` against `allowed_providers`.

Raises `FileNotFoundError` if the file does not exist. Raises `KeyError` if a required field is missing.

## `provider_client`

### `load_provider(provider_name, allowed_providers) -> ProviderModule`

Dynamically imports `<provider_name>_client.py` and validates it implements `create_client`, `preload`, `send_request`, and `extract_content`.

Raises `ValueError` if `provider_name` is not in `allowed_providers` or is not a valid identifier. Raises `TypeError` if the imported module is missing a required function.

## `prompt_loader`

### `load_prompt(prompt_path) -> str`

Reads and returns the stripped text content of a prompt file.

Raises `FileNotFoundError` if the file does not exist.

## Provider modules (`ollama_client`, `openai_client`, `anthropic_client`)

Each provider module implements the same four functions:

### `create_client(config) -> Any`

Creates and returns the provider's SDK client (`None` for Ollama, which uses direct HTTP requests).

### `preload(config, client) -> None`

Performs provider-specific startup work. Ollama loads the model into memory; OpenAI and Anthropic verify the configured model exists via `client.models.retrieve(config.model)`.

### `send_request(config, client, system_prompt, messages) -> Any`

Sends a chat request to the provider. Returns the raw provider response.

### `extract_content(response) -> str`

Extracts the assistant's text content from a provider response.


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
