# Usage Guide

## Starting the Orchestrator

```bash
source .venv/bin/activate
python main.py
```

On startup, the orchestrator:
1. Loads configuration from `config.json`
2. Reads the system prompt
3. Preloads the configured model into Ollama using the configured `keep_alive`
4. Creates a new chat session file
5. Starts the web UI

## Using the Web UI

```
Loading Ollama model ministral-3:8b...
KIO1 Orchestrator web UI: http://127.0.0.1:5000
```

Open that address in a browser. Type a request in the message box at the bottom and press Enter (or click Send). The orchestrator sends it to Ollama and renders the response in the conversation. The input is ready for another query when the response completes.

Workflow-plan responses are rendered as numbered step cards with the agent, capability, task, and explanation. Other JSON responses are shown as readable labeled fields and lists. Raw model responses are printed in the terminal running KIO1; they are not shown in the dashboard. Invalid JSON is reported as an error in the conversation, with the raw response available in the server terminal.

Use **New chat** in the history sidebar to start a separate conversation. Select a saved conversation to restore its messages and continue with its existing context. Conversation titles come from the first user query. The history is shared by this single-operator app instance; it is not separated by user account.

Each chat is saved as a JSONL file in the configured chat directory. User and assistant messages are restored when a saved conversation is opened; the system prompt is kept out of the browser and supplied separately to the model.

Override the bind address with the `KIO1_WEB_HOST` and `KIO1_WEB_PORT` environment variables, for example to expose the UI on all interfaces:

```bash
KIO1_WEB_HOST=0.0.0.0 KIO1_WEB_PORT=8080 python main.py
```

## Uploading Files

Use the Model, Dataset, or File buttons next to the message box to upload a model checkpoint, dataset, or other file. Each button saves into its own subfolder (`assets/models/`, `assets/datasets/`, `assets/files/`) with a sanitized, collision-resistant filename; the UI reports the stored path once the upload completes. The `assets/` directory is not tracked by git or baked into the Docker image.

## Example Queries

### Full development lifecycle

```
> Build a REST API for user management with authentication
```

The dashboard presents this as a workflow summary with six numbered agent steps and an explanation. The raw JSON is printed to the server terminal and saved as the assistant message in the chat history. KIO1 formats and displays the plan; it does not validate or dispatch the proposed workflow.

### Bug fix request

```
> Fix the null pointer exception in the payment module
```

### Testing only

```
> Write unit tests for the notification service
```

## Saved Conversations

Each saved conversation keeps its own context. Continue in the current chat or select an earlier one from the history sidebar:

```
> Design a drone telemetry system
> Now add security review to the plan
```

## Exiting

Close the browser tab, or stop the server with `Ctrl+C` in the terminal running `main.py`.

## Chat History Files

Each conversation produces a JSONL file in the configured chat directory. See [Chat History Format](chat-history.md) for details.

