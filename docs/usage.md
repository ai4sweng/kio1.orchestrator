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
5. Starts the operator API

## Using the Web UI

```
Loading Ollama model ministral-3:8b...
KIO1 Orchestrator API: http://127.0.0.1:5000
```

That startup URL is the API, not a browser page. Start the independent UI container as described in [Independent Deployment](deployment.md), then open its published `http://127.0.0.1:5000` address. Type a request and click Send. UI proxies to KIO1, which sends it to Ollama and returns the response. The input is ready for another query when the response completes.

Workflow-plan responses are rendered as numbered step cards with the agent, capability, task, and explanation. Other JSON responses are shown as readable labeled fields and lists. Raw model responses are printed in the terminal running KIO1; they are not shown in the dashboard. Invalid JSON is reported as an error in the conversation, with the raw response available in the server terminal.

Click a step's task text to edit it. Each selected KIO shows all capabilities from its catalog as checkboxes, with the LLM's choice selected initially. Capability labels use spaces and an initial capital, for example **Generate vitis config**; identifiers sent to agents remain unchanged. Select additional capabilities or deselect existing ones before running. Keep at least one selected capability and a nonblank task of at most 16000 characters per step. Use a step's trash button to delete that entry before dispatch. Deleted entries are not sent to agents, and references to their step IDs are removed from the remaining dependencies. Other steps and structured inputs are retained; sequential workflows still run remaining steps in order. An empty workflow cannot run. **Run workflow** uses the current edited plan. Editors and deletion buttons are disabled while requests are in progress. These edits are not saved to chat history: reloading or switching conversations restores the original LLM response. Deleting an entry does not cancel a task that has already been sent.

For each completed inference, the terminal prints labeled blocks in this order: the user's query, the raw LLM response, and the formatted workflow JSON. If the response cannot be formatted, the query and raw response are still printed.

When **Run workflow** dispatches a task, a separate **KIO1 agent payload JSON** block shows the actual request body sent to that KIO, including its inputs and dependency results. These payloads are finalized during execution, not generation; authentication headers are never printed. Terminal output may contain sensitive task data, so redact it before sharing.

Use **New chat** in the history sidebar to start a separate conversation. Select a saved conversation to restore its messages and continue with its existing context. Conversation titles come from the first user query. The history is shared by this single-operator app instance; it is not separated by user account.

Use the trash icon beside a saved conversation and confirm to permanently delete it. Deleting the active conversation starts a new empty chat. Uploaded files and server logs are not deleted.

Each chat is saved as a JSONL file in the configured chat directory. User and assistant messages are restored when a saved conversation is opened; the system prompt is kept out of the browser and supplied separately to the model.

Override the API bind address with `KIO1_WEB_HOST` and `KIO1_WEB_PORT` when needed. These do not configure the independent UI's published port. Restrict any host API listener to trusted clients:

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

The dashboard presents this as a workflow summary with numbered agent steps and an explanation. The raw JSON is printed to the server terminal and saved as the assistant message in the chat history. Generating a plan does not execute it.

Container deployments started with `make run` or `make up` use the bundled dummy registry by default, so **Run workflow** is available when the UI loads. Start dummy-kios to receive tasks. For another trusted registry, set `AGENT_CONFIG=agents.json`; recreate KIO1 after changing its registry. Local `python main.py` requires `KIO1_AGENT_CONFIG` to be set explicitly. Select **Run workflow** and confirm. The planner validates the complete plan before sending tasks; successful dependency results are forwarded to dependent steps. Failed or unknown outcomes stop new dispatches. Results are displayed but are not persisted in chat history. See the [container contract](workflow-contract.md) for setup and limitations.

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
