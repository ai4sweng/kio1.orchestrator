# Chat History Format

## Overview

Each conversation creates a JSONL (JSON Lines) file in the configured chat directory. Every line is a self-contained JSON object representing one message. The initial conversation is created at startup; additional conversations can be created from the web UI.

## File Naming

```
chat_<YYYYMMDD>_<HHMMSS>_<8-char-uuid>.jsonl
```

Example: `chat_20260724_101706_5dd461f5.jsonl`

The id follows the `session_logger.generate_session_id()` format. The initial conversation ID is also used for the startup log file. Conversations created in the browser have their own IDs but do not create separate log files. A session that fails during startup can produce a log file with no matching chat file.

## Message Schema

Each line follows the format:

```json
{"role": "<role>", "content": "<message text>"}
```

| Role | Description |
|------|-------------|
| `system` | System prompt (always line 1) |
| `user` | User input |
| `assistant` | Model response, commonly a JSON workflow plan |

## Example File

```jsonl
{"role": "system", "content": "You are KIO1, an AI Orchestrator..."}
{"role": "user", "content": "Build a REST API for user management"}
{"role": "assistant", "content": "{\"workflow_id\": \"wf-usr01\", ...}"}
{"role": "user", "content": "Add security review"}
{"role": "assistant", "content": "{\"workflow_id\": \"wf-usr02\", ...}"}
```

## Behavior

- A new file is created at startup and whenever the user selects **New chat**.
- The system prompt is written as the first line on file creation.
- Messages are appended after each successful exchange.
- The history sidebar titles chats from their first user message and lets the user reopen them.
- On activation, user and assistant messages are restored; the system prompt is excluded from the browser response and supplied separately to the model.
- Messages are append-only until a conversation is deleted using its trash icon
	and confirmation. Deletion removes that conversation's JSONL file permanently.
	Deleting the active conversation creates and activates a new empty one first.
	Uploaded files, logs, and work already sent to containers are unaffected.
- The initial session's log file (`logs/log_<same-id>.log`) records timing, token counts, and errors. Browser-created conversation IDs do not have paired log files.
- Conversation files are shared within the current single-operator app instance; they are not partitioned by user identity.

