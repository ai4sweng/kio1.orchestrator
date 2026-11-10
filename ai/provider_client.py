"""Provider selection and authenticated OpenAI-compatible chat transport."""

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from ai import ollama_client
from ai.ollama_client import extract_content
from config_loader import Config, resolve_api_key
from session_logger import measure_duration
from telemetry import record_gen_ai_response

logger = logging.getLogger("main")
HOSTED_ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
MAX_RESPONSE_BYTES = 1024 * 1024
__all__ = ["extract_content", "preload_model", "send_request"]


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent forwarding authenticated requests to another endpoint."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        """Reject an HTTP redirect.

        Args:
            req: Original request.
            fp: Response stream.
            code: Redirect status.
            msg: HTTP status message.
            headers: Response headers.
            newurl: Redirect destination.

        Returns:
            None so urllib raises an HTTP error instead of following.
        """
        return None


def preload_model(config: Config) -> None:
    """Preload local models only; hosted services load models themselves.

    Args:
        config: Selected provider and model settings.

    Returns:
        None.
    """
    if config.provider == "ollama":
        ollama_client.preload_model(config)
    elif config.provider in HOSTED_ENDPOINTS:
        logger.info(
            "Model preload skipped: provider=%s model=%s reason=hosted_provider",
            config.provider,
            config.model,
        )
    else:
        raise ValueError("Unsupported model provider.")


def send_request(
    config: Config,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Send a non-streaming JSON completion using the selected provider.

    Args:
        config: Selected provider and model settings.
        system_prompt: System instruction text.
        messages: Conversation messages excluding the system prompt.

    Returns:
        A response with an Ollama-compatible message containing assistant content.

    Raises:
        ValueError: Credentials, response shape, or completion status are invalid.
        RuntimeError: Hosted HTTP or transport failure; contains no remote body.
    """
    if config.provider == "ollama":
        return ollama_client.send_request(config, system_prompt, messages)
    if config.provider not in HOSTED_ENDPOINTS:
        raise ValueError("Unsupported model provider.")

    try:
        api_key = resolve_api_key(config.provider, config.api_key, config.api_key_env)
    except ValueError:
        logger.error("Provider credentials invalid: provider=%s", config.provider)
        raise

    payload = {
        "model": config.model,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
        "temperature": config.temperature,
        "max_tokens": config.max_output_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        HOSTED_ENDPOINTS[config.provider],
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "KIO1-Orchestrator/1.0",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    logger.info(
        "Provider request started: provider=%s model=%s message_count=%d",
        config.provider,
        config.model,
        len(messages) + 1,
    )
    phase = "connect"
    http_status = None
    with measure_duration() as elapsed:
        try:
            opener = urllib.request.build_opener(_RejectRedirects())
            with opener.open(request, timeout=config.request_timeout) as response:
                http_status = response.status
                phase = "read_response"
                raw_response = response.read(MAX_RESPONSE_BYTES + 1)
            phase = "validate_response"
            if len(raw_response) > MAX_RESPONSE_BYTES:
                raise ValueError("Provider response exceeds the 1 MiB limit.")
            try:
                data = json.loads(raw_response)
            except (ValueError, UnicodeError):
                raise ValueError("Provider returned invalid JSON.") from None
            if not isinstance(data, dict) or "error" in data:
                raise ValueError("Provider returned an error or invalid response.")
            choices = data.get("choices")
            if (
                not isinstance(choices, list)
                or not choices
                or not isinstance(choices[0], dict)
            ):
                raise ValueError("Provider response has no valid completion choice.")
            choice = choices[0]
            finish_reason = choice.get("finish_reason")
            usage = data.get("usage")
            usage = usage if isinstance(usage, dict) else {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            input_tokens = (
                input_tokens
                if type(input_tokens) is int and input_tokens >= 0
                else None
            )
            output_tokens = (
                output_tokens
                if type(output_tokens) is int and output_tokens >= 0
                else None
            )
            response_model = data.get("model")
            response_id = data.get("id")
            record_gen_ai_response(
                provider=config.provider,
                request_model=config.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                response_model=(
                    response_model[:256] if isinstance(response_model, str) else None
                ),
                response_id=response_id[:256] if isinstance(response_id, str) else None,
                finish_reason=(
                    finish_reason
                    if finish_reason
                    in ("stop", "length", "content_filter", "tool_calls")
                    else None
                ),
                truncated=finish_reason == "length",
                truncation_reason="length",
            )
            if finish_reason == "length":
                raise ValueError(
                    "Response truncated before completion; increase max_output_tokens "
                    "or shorten the conversation."
                )
            message = choice.get("message")
            if finish_reason != "stop" or not isinstance(message, dict):
                raise ValueError(
                    "Provider did not return a completed assistant message."
                )
            content = message.get("content")
            if (
                message.get("refusal")
                or not isinstance(content, str)
                or not content.strip()
            ):
                raise ValueError(
                    "Provider returned empty content or refused the request."
                )
        except Exception as error:
            if isinstance(error, urllib.error.HTTPError):
                http_status = error.code
                phase = "http_status"
                error.close()
            logger.error(
                "Provider request failed: provider=%s model=%s phase=%s "
                "http_status=%s error_type=%s duration_ms=%d",
                config.provider,
                config.model,
                phase,
                http_status,
                type(error).__name__,
                elapsed(),
            )
            if isinstance(error, ValueError) and phase == "validate_response":
                raise
            hint = (
                "OpenRouter reports insufficient credits or an unaffordable request. "
                "Check account credits and the API-key spending limit, add credits, "
                "reduce max_output_tokens, or explicitly select a free model. "
                f"Requested output budget: {config.max_output_tokens} tokens."
                if config.provider == "openrouter" and http_status == 402
                else "Check credentials, model availability, rate limits, and connectivity."
            )
            raise RuntimeError(
                f"{config.provider} request failed (HTTP {http_status}, phase={phase}). "
                f"{hint}"
            ) from None

    logger.info(
        "Response received: provider=%s model=%s duration_ms=%d http_status=%s "
        "input_tokens=%s output_tokens=%s finish_reason=stop",
        config.provider,
        config.model,
        elapsed(),
        http_status,
        input_tokens,
        output_tokens,
    )
    return {"message": {"role": "assistant", "content": content}}
