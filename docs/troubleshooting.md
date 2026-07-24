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

## Reading the logs

Each run writes `logs/log_<session_id>.log`, where `session_id` is `<YYYYMMDD>_<HHMMSS>_<8-char-uuid>`. The same id names that session's `chats/chat_<session_id>.jsonl`, so the transcript and the log can be read side by side.

Each line is UTC ISO 8601 with milliseconds, then the session id, level, module, and message:

```
2026-07-24T09:19:43.465Z 20260724_101943_ff849b40 INFO main Session started: session_id=20260724_101943_ff849b40
2026-07-24T09:19:43.466Z 20260724_101943_ff849b40 INFO config_loader Config loaded: provider=ollama model=ministral-3:8b prompt_path=prompts/kio1_system_prompt_ver1.txt
2026-07-24T09:19:43.712Z 20260724_101943_ff849b40 INFO ollama_client Model preloaded: model=ministral-3:8b duration_ms=199
2026-07-24T09:19:56.220Z 20260724_101943_ff849b40 INFO main Turn started: turn=1
2026-07-24T09:21:15.523Z 20260724_101943_ff849b40 INFO ollama_client Response received: model=ministral-3:8b duration_ms=79303 input_tokens=2280 output_tokens=550
2026-07-24T09:21:20.108Z 20260724_101943_ff849b40 INFO main Session ended: turns=1
```

Timestamps are UTC, matching the `created_at` values inside provider responses. The console shows only `ERROR`; everything else lives in the file.

| Level | Contents |
|-------|----------|
| `DEBUG` | Full request payloads and model responses, including message text |
| `INFO` | Session and turn markers, config, provider loaded, model preloaded/verified, per-request duration and token counts |
| `WARNING` | Recoverable issues, such as the formatter falling back to `ast.literal_eval` |
| `ERROR` | Startup failures and failed requests, with tracebacks |

Useful greps:

```bash
LOG=logs/$(ls -t logs/ | head -1)

grep "Response received:" $LOG   # duration and tokens per turn
grep -c "Turn started:" $LOG     # turns attempted this session
tail -1 $LOG                     # "Session ended: turns=N" if it exited cleanly
grep ERROR logs/*.log            # failures across all sessions
```

A log containing `Startup failed` with no `Session ended` line never reached the prompt. A log with neither was killed mid-session.

`input_tokens` grows every turn because the whole transcript is re-sent, while `output_tokens` stays roughly flat — the two diverging shows the cost of replaying conversation history.

Compare `Model preloaded:` against `Response received:` durations to separate startup cost from request cost. With `keep_alive: -1` the model load is paid once at startup rather than per request.
