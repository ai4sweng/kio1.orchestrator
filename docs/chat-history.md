# Chat History Format

## Overview

Each orchestrator session creates a JSONL (JSON Lines) file in the `chats/` directory. Every line is a self-contained JSON object representing one message.

## File Naming

```
chat_<YYYYMMDD>_<HHMMSS>_<8-char-uuid>.jsonl
```

Example: `chat_20260710_172641_c2d9177c.jsonl`

## Message Schema

Each line follows the format:

```json
{"role": "<role>", "content": "<message text>"}
```

| Role | Description |
|------|-------------|
| `system` | System prompt (always line 1) |
| `user` | User input |
| `assistant` | Model response (JSON workflow plan) |

## Example File

```jsonl
{"role": "system", "content": "You are KIO1, an AI Orchestrator..."}
{"role": "user", "content": "Build a REST API for user management"}
{"role": "assistant", "content": "{\"workflow_id\": \"wf-usr01\", ...}"}
{"role": "user", "content": "Add security review"}
{"role": "assistant", "content": "{\"workflow_id\": \"wf-usr02\", ...}"}
```

## Behavior

- A new file is created at the start of each session.
- The system prompt is written as the first line on file creation.
- Messages are appended after each successful exchange.
- On conversation reload, the system prompt line is excluded from the message list sent to the model (it is injected separately in the API payload).
- Files are never modified or deleted by the application; they accumulate as an audit trail.
