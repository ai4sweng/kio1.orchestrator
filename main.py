import logging
import os
import sys
import urllib.request

from ai.ollama_client import (
    PROVIDER_NAME,
    extract_content,
    preload_model,
    send_request,
)
from api.webapp import create_app
from chat_history import create_chat_file
from components.chat_service import run_turn
from config_loader import load_config
from prompt_loader import load_prompt
from session_logger import generate_session_id, init_logger, measure_duration
from telemetry import (
    init_telemetry,
    record_session_completed,
    record_session_started,
    shutdown_telemetry,
    trace_operation,
    trace_provider_preload,
)

logger = logging.getLogger("main")
__all__ = [
    "PROVIDER_NAME",
    "extract_content",
    "main",
    "preload_model",
    "run_turn",
    "send_request",
    "urllib",
]


def main() -> None:
    """Load configuration, preload the model, and serve the operator API.

    Args:
        None. Reads config.json and KIO1 environment overrides at startup.

    Returns:
        None after the web server stops and telemetry is shut down.

    Raises:
        SystemExit: Startup fails; the process exits with status 1.
    """
    try:
        session_id = generate_session_id()
        init_logger("logs", session_id)
        print(f"Session log: logs/log_{session_id}.log")
        logger.info("Session started: session_id=%s", session_id)

        config = load_config()
        init_telemetry(config.telemetry, llm=config.model)

        startup_attributes = {
            "gen_ai.provider.name": PROVIDER_NAME,
            "gen_ai.request.model": config.model,
            "gen_ai.conversation.id": session_id,
        }

        with trace_operation("kio1.startup", startup_attributes):
            system_prompt = load_prompt(config.prompt_path)

            print(f"Loading Ollama model {config.model}...")
            with trace_provider_preload(
                provider=PROVIDER_NAME,
                model=config.model,
            ):
                preload_model(config)

            chat_file = create_chat_file(
                config.chat_directory, system_prompt, session_id
            )
            record_session_started(
                provider=PROVIDER_NAME,
                model=config.model,
            )

    except Exception:
        logger.exception("Startup failed")
        print(
            "Error: failed to start orchestrator. Check logs for details.",
            file=sys.stderr,
        )
        shutdown_telemetry()
        sys.exit(1)

    app = create_app(
        config=config,
        system_prompt=system_prompt,
        chat_file=chat_file,
        session_id=session_id,
    )

    host = os.environ.get("KIO1_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("KIO1_WEB_PORT", "5000"))

    print(f"KIO1 Orchestrator API: http://{host}:{port}")

    session_success = False
    with measure_duration() as session_elapsed:
        try:
            # threaded=True: Werkzeug's dev server otherwise handles one request
            # at a time, so a file upload would stall behind an in-flight
            # (potentially slow) Ollama chat request.
            app.run(
                host=host, port=port, debug=False, use_reloader=False, threaded=True
            )
            session_success = True
        finally:
            record_session_completed(
                provider=PROVIDER_NAME, model=config.model, success=session_success
            )
            logger.info("Session ended: duration_ms=%d", session_elapsed())
            shutdown_telemetry()


if __name__ == "__main__":
    main()
