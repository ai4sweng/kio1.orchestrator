# Troubleshooting

## Provider module not found

If the configuration contains:

```json
"provider": "example"
```

the project must contain:

```text
example_client.py
```

## Ollama endpoint missing

Ollama requires:

```json
"provider_options": {
    "endpoint": "http://localhost:11434"
}
```

## OpenAI authentication error

Confirm the key is set:

```bash
python3 -c 'import os; print("set" if os.getenv("OPENAI_API_KEY") else "missing")'
```

## Anthropic authentication error

Confirm the key is set:

```bash
python3 -c 'import os; print("set" if os.getenv("ANTHROPIC_API_KEY") else "missing")'
```

## Model not found

Confirm that the configured model ID exists and is available to the selected provider account.

## Invalid JSON response

The system prompt asks providers to return JSON only. The formatter also removes optional Markdown JSON fences before parsing provider output.
