# Architecture

## Overview

KIO1 Orchestrator is a terminal application that accepts natural-language user requests and produces structured JSON workflow plans. It routes tasks to specialized KIO agents (KIO2–KIO13) by leveraging a local Ollama LLM instance.

## System Diagram

```mermaid
graph TD
    User[User Terminal] -->|query| Main[main.py]
    Main --> ConfigLoader[config_loader.py]
    Main --> PromptLoader[prompt_loader.py]
    Main --> ChatHistory[chat_history.py]
    Main --> OllamaClient[ollama_client.py]
    Main --> Formatter[formatter.py]
    OllamaClient -->|HTTP POST| Ollama[Ollama API :11434]
    ChatHistory -->|read/write| ChatFiles[chats/*.jsonl]
    ConfigLoader -->|read| ConfigFile[config.json]
    PromptLoader -->|read| PromptFile[prompts/*.txt]
```

## Components

| Module | Responsibility |
|--------|---------------|
| `main.py` | Entry point; REPL loop, orchestrates all other modules |
| `config_loader.py` | Loads and validates `config.json` into a typed `Config` dataclass |
| `prompt_loader.py` | Reads system prompt text from disk |
| `ollama_client.py` | Sends HTTP requests to the Ollama `/api/chat` endpoint |
| `chat_history.py` | Manages JSONL-based conversation persistence |
| `formatter.py` | Pretty-prints JSON responses for terminal display |

## Data Flow

1. On startup, configuration and system prompt are loaded.
2. The configured model is preloaded into Ollama's memory (`keep_alive: -1`).
3. A new JSONL chat file is created for the session.
4. The REPL loop reads user input, loads prior messages from the chat file, sends the full conversation to Ollama, and displays the formatted JSON workflow plan.
5. Both user and assistant messages are appended to the chat file after each exchange.

## Design Decisions

- **No external HTTP library**: Uses `urllib.request` from the standard library to minimize dependencies.
- **JSONL chat storage**: Each message is a single JSON line, enabling append-only writes and simple streaming reads.
- **Model preloading**: The model is loaded into Ollama memory at startup with `keep_alive: -1` (indefinite) to eliminate cold-start latency on the first query.
- **JSON-forced output**: The Ollama request includes `"format": "json"` to guarantee structured responses from the model.
