import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Counter, Histogram, Meter, UpDownCounter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer
from opentelemetry.util.types import Attributes, AttributeValue

from config_loader import TelemetryConfig

logger = logging.getLogger(__name__)

_INSTRUMENTATION_SCOPE = "kio1.orchestrator"

_GEN_AI_DURATION_BUCKETS = (
    0.01,
    0.02,
    0.04,
    0.08,
    0.16,
    0.32,
    0.64,
    1.28,
    2.56,
    5.12,
    10.24,
    20.48,
    40.96,
    81.92,
    163.84,
    327.68,
)

_TOKEN_USAGE_BUCKETS = (
    1,
    4,
    16,
    64,
    256,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
    67108864,
)

_ALLOWED_EXECUTION_MODES = {"sequential", "parallel", "mixed"}
_ALLOWED_TRUNCATION_REASONS = {
    "max_output_tokens",
    "context_window",
    "length",
}

# AI4SWENG Observability Integration Contract v1.0, section 2.1: mandatory
# metric set, expressed in milliseconds to match kio_request_duration_ms.
_REQUEST_DURATION_MS_BUCKETS = tuple(
    bucket * 1000 for bucket in _GEN_AI_DURATION_BUCKETS
)

_HEARTBEAT_INTERVAL_SECONDS = 60.0

# Rough, non-billing-accurate USD-per-1K-token estimate used only because the
# contract's kio_llm_cost_usd metric is mandatory and none of the providers
# KIO1 talks to expose real invoice data locally. Mirrors the simulated
# per-model coefficients used by the platform's kio3/kio4 KIO simulators.
_COST_PER_1K_TOKENS_USD: dict[str, float] = {
    "ollama": 0.0,
    "openai": 0.002,
    "anthropic": 0.003,
}


@dataclass(frozen=True)
class _Instruments:
    """OpenTelemetry metric instruments used by the orchestrator."""

    sessions_started: Counter
    sessions_completed: Counter
    turns: Counter
    turn_duration: Histogram
    provider_preloads: Counter
    provider_preload_duration: Histogram
    gen_ai_operation_duration: Histogram
    gen_ai_token_usage: Histogram
    conversation_messages: Histogram
    response_truncations: Counter
    format_fallbacks: Counter
    workflow_plans: Counter
    workflow_step_count: Histogram

    # AI4SWENG Observability Integration Contract v1.0, section 2.1:
    # mandatory metric set, one instrument per contract metric name.
    request_count: Counter
    request_duration_ms: Histogram
    request_error_count: Counter
    llm_token_count: Counter
    llm_cost_usd: Counter
    session_active_count: UpDownCounter
    heartbeat: Counter


_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_tracer: Tracer = trace.get_tracer(_INSTRUMENTATION_SCOPE)
_meter: Meter = metrics.get_meter(_INSTRUMENTATION_SCOPE)
_instruments: _Instruments | None = None
_heartbeat_thread: threading.Thread | None = None
_heartbeat_stop_event: threading.Event | None = None


def _create_instruments(meter: Meter) -> _Instruments:
    """Create all application metric instruments."""

    return _Instruments(
        sessions_started=meter.create_counter(
            "kio1.sessions.started",
            unit="{session}",
            description="Number of orchestrator sessions started.",
        ),
        sessions_completed=meter.create_counter(
            "kio1.sessions.completed",
            unit="{session}",
            description="Number of orchestrator sessions completed.",
        ),
        turns=meter.create_counter(
            "kio1.turns",
            unit="{turn}",
            description="Number of user turns processed.",
        ),
        turn_duration=meter.create_histogram(
            "kio1.turn.duration",
            unit="s",
            description="End-to-end duration of a user turn.",
            explicit_bucket_boundaries_advisory=_GEN_AI_DURATION_BUCKETS,
        ),
        provider_preloads=meter.create_counter(
            "kio1.provider.preloads",
            unit="{preload}",
            description="Number of provider preload operations.",
        ),
        provider_preload_duration=meter.create_histogram(
            "kio1.provider.preload.duration",
            unit="s",
            description="Duration of provider preload operations.",
            explicit_bucket_boundaries_advisory=_GEN_AI_DURATION_BUCKETS,
        ),
        gen_ai_operation_duration=meter.create_histogram(
            "gen_ai.client.operation.duration",
            unit="s",
            description="Duration of generative AI client operations.",
            explicit_bucket_boundaries_advisory=_GEN_AI_DURATION_BUCKETS,
        ),
        gen_ai_token_usage=meter.create_histogram(
            "gen_ai.client.token.usage",
            unit="{token}",
            description="Number of input and output tokens used.",
            explicit_bucket_boundaries_advisory=_TOKEN_USAGE_BUCKETS,
        ),
        conversation_messages=meter.create_histogram(
            "kio1.conversation.messages",
            unit="{message}",
            description="Number of messages supplied to a model request.",
        ),
        response_truncations=meter.create_counter(
            "kio1.response.truncations",
            unit="{response}",
            description="Number of model responses truncated before completion.",
        ),
        format_fallbacks=meter.create_counter(
            "kio1.response.format_fallbacks",
            unit="{response}",
            description="Number of responses requiring the Python-literal fallback.",
        ),
        workflow_plans=meter.create_counter(
            "kio1.workflow.plans",
            unit="{plan}",
            description="Number of workflow plans successfully parsed.",
        ),
        workflow_step_count=meter.create_histogram(
            "kio1.workflow.step.count",
            unit="{step}",
            description="Number of steps in generated workflow plans.",
        ),
        request_count=meter.create_counter(
            "kio.request.count",
            unit="1",
            description=(
                "Total number of requests processed (contract metric "
                "kio_request_count)."
            ),
        ),
        request_duration_ms=meter.create_histogram(
            "kio.request.duration_ms",
            unit="ms",
            description=(
                "End-to-end request latency distribution (contract metric "
                "kio_request_duration_ms)."
            ),
            explicit_bucket_boundaries_advisory=_REQUEST_DURATION_MS_BUCKETS,
        ),
        request_error_count=meter.create_counter(
            "kio.request.error_count",
            unit="1",
            description=(
                "Errors categorized by a bounded error type (contract "
                "metric kio_request_error_count)."
            ),
        ),
        llm_token_count=meter.create_counter(
            "kio.llm.token_count",
            unit="{token}",
            description=(
                "LLM token usage by direction (contract metric "
                "kio_llm_token_count)."
            ),
        ),
        llm_cost_usd=meter.create_counter(
            "kio.llm.cost_usd",
            unit="USD",
            description=(
                "Estimated cumulative LLM cost (contract metric "
                "kio_llm_cost_usd). Derived from a fixed per-provider "
                "USD/1K-token coefficient, not real billing data."
            ),
        ),
        session_active_count=meter.create_up_down_counter(
            "kio.session.active_count",
            unit="1",
            description=(
                "Number of currently active sessions (contract metric "
                "kio_session_active_count)."
            ),
        ),
        heartbeat=meter.create_counter(
            "kio.heartbeat",
            unit="1",
            description=(
                "Background liveness signal incremented every "
                f"{int(_HEARTBEAT_INTERVAL_SECONDS)} seconds while "
                "telemetry is enabled (contract metric kio_heartbeat)."
            ),
        ),
    )


def init_telemetry(config: TelemetryConfig) -> None:
    """Initialize OTLP trace and metric exporters.

    Args:
        config: Validated telemetry configuration.

    Returns:
        None.
    """
    global _instruments
    global _meter
    global _meter_provider
    global _tracer
    global _tracer_provider

    if not config.enabled:
        logger.info("OpenTelemetry export is disabled")
        return

    if _tracer_provider is not None or _meter_provider is not None:
        logger.debug("OpenTelemetry is already initialized")
        return

    resource = Resource.create(
        {
            "service.name": config.service_name,
            "kio.id": config.kio_id,
            "deployment.environment": config.deployment_environment,
        }
    )

    trace_exporter = OTLPSpanExporter(
        endpoint=f"{config.otlp_http_endpoint}/v1/traces",
    )
    metric_exporter = OTLPMetricExporter(
        endpoint=f"{config.otlp_http_endpoint}/v1/metrics",
    )

    tracer_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(
            TraceIdRatioBased(config.trace_sample_ratio),
        ),
        shutdown_on_exit=False,
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(trace_exporter),
    )

    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=config.metric_export_interval_ms,
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
        shutdown_on_exit=False,
    )

    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)

    _tracer_provider = tracer_provider
    _meter_provider = meter_provider
    _tracer = tracer_provider.get_tracer(_INSTRUMENTATION_SCOPE)
    _meter = meter_provider.get_meter(_INSTRUMENTATION_SCOPE)
    _instruments = _create_instruments(_meter)

    _start_heartbeat(_instruments)

    logger.info(
        "OpenTelemetry initialized: service=%s endpoint=%s",
        config.service_name,
        config.otlp_http_endpoint,
    )


def _heartbeat_loop(instruments: _Instruments, stop_event: threading.Event) -> None:
    """Increment the heartbeat counter every interval until stopped.

    Args:
        instruments: The metric instruments to record onto.
        stop_event: Signaled to stop the loop and exit the thread promptly.

    Returns:
        None.
    """
    while not stop_event.wait(_HEARTBEAT_INTERVAL_SECONDS):
        try:
            instruments.heartbeat.add(1)
        except Exception:
            logger.exception("Failed to record the kio.heartbeat metric")


def _start_heartbeat(instruments: _Instruments) -> None:
    """Start the background heartbeat thread, replacing any existing one."""

    global _heartbeat_thread
    global _heartbeat_stop_event

    _stop_heartbeat()

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_loop,
        args=(instruments, stop_event),
        name="kio1-otel-heartbeat",
        daemon=True,
    )
    _heartbeat_stop_event = stop_event
    _heartbeat_thread = thread
    thread.start()


def _stop_heartbeat() -> None:
    """Signal the heartbeat thread to stop and wait briefly for it to exit."""

    global _heartbeat_thread
    global _heartbeat_stop_event

    if _heartbeat_stop_event is not None:
        _heartbeat_stop_event.set()

    if _heartbeat_thread is not None:
        _heartbeat_thread.join(timeout=1.0)

    _heartbeat_thread = None
    _heartbeat_stop_event = None


def shutdown_telemetry() -> None:
    """Flush pending telemetry and shut down exporters safely."""

    _stop_heartbeat()

    meter_provider = _meter_provider
    tracer_provider = _tracer_provider

    if meter_provider is not None:
        try:
            meter_provider.shutdown()
        except Exception:
            logger.exception("Failed to shut down the OpenTelemetry meter provider")

    if tracer_provider is not None:
        try:
            tracer_provider.shutdown()
        except Exception:
            logger.exception("Failed to shut down the OpenTelemetry tracer provider")


def _model_attributes(provider: str, model: str) -> dict[str, AttributeValue]:
    """Return low-cardinality provider and model attributes."""

    return {
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
    }


def _mark_span_error(span: Span, error: BaseException) -> None:
    """Mark a span as failed without exporting the exception message."""

    span.set_attribute("error.type", type(error).__name__)
    span.set_status(Status(StatusCode.ERROR))


@contextmanager
def trace_operation(
    name: str,
    attributes: Attributes = None,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
) -> Iterator[Span]:
    """Create a span and attach privacy-safe error information."""

    with _tracer.start_as_current_span(
        name,
        kind=kind,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except BaseException as error:
            _mark_span_error(span, error)
            raise


@contextmanager
def trace_turn(
    *,
    session_id: str,
    turn_number: int,
    provider: str,
    model: str,
) -> Iterator[Span]:
    """Trace and measure one complete user turn."""

    started_at = perf_counter()
    status = "success"
    error_type: str | None = None

    span_attributes: dict[str, AttributeValue] = {
        **_model_attributes(provider, model),
        "gen_ai.conversation.id": session_id,
        "kio1.turn.number": turn_number,
    }

    with trace_operation("kio1.turn", span_attributes) as span:
        try:
            yield span
        except BaseException as error:
            status = "error"
            error_type = type(error).__name__
            raise
        finally:
            instruments = _instruments
            if instruments is not None:
                metric_attributes = _model_attributes(provider, model)
                metric_attributes["kio1.status"] = status

                if error_type is not None:
                    metric_attributes["error.type"] = error_type

                elapsed_seconds = perf_counter() - started_at
                instruments.turns.add(1, metric_attributes)
                instruments.turn_duration.record(
                    elapsed_seconds,
                    metric_attributes,
                )

                # Contract mandatory metrics (kio_request_count,
                # kio_request_duration_ms, kio_request_error_count): one
                # "request" is one complete user turn, mirroring the
                # platform's kio-simulator request definition.
                instruments.request_count.add(1, {"status": status})
                instruments.request_duration_ms.record(elapsed_seconds * 1000)

                if error_type is not None:
                    instruments.request_error_count.add(
                        1, {"error_type": error_type}
                    )


@contextmanager
def trace_provider_preload(
    *,
    provider: str,
    model: str,
) -> Iterator[Span]:
    """Trace and measure model preload or provider validation."""

    started_at = perf_counter()
    status = "success"
    error_type: str | None = None
    span_attributes = _model_attributes(provider, model)

    with trace_operation("kio1.provider.preload", span_attributes) as span:
        try:
            yield span
        except BaseException as error:
            status = "error"
            error_type = type(error).__name__
            raise
        finally:
            instruments = _instruments
            if instruments is not None:
                metric_attributes = _model_attributes(provider, model)
                metric_attributes["kio1.status"] = status

                if error_type is not None:
                    metric_attributes["error.type"] = error_type

                instruments.provider_preloads.add(1, metric_attributes)
                instruments.provider_preload_duration.record(
                    perf_counter() - started_at,
                    metric_attributes,
                )


@contextmanager
def trace_gen_ai_request(
    *,
    provider: str,
    model: str,
    session_id: str,
    turn_number: int,
    message_count: int,
    max_output_tokens: int,
    temperature: float,
) -> Iterator[Span]:
    """Trace and measure one non-streaming model request."""

    started_at = perf_counter()
    error_type: str | None = None

    span_attributes: dict[str, AttributeValue] = {
        **_model_attributes(provider, model),
        "gen_ai.operation.name": "chat",
        "gen_ai.conversation.id": session_id,
        "gen_ai.request.max_tokens": max_output_tokens,
        "gen_ai.request.temperature": temperature,
        "gen_ai.request.is_streaming": False,
        "gen_ai.output.type": "json",
        "kio1.turn.number": turn_number,
        "kio1.conversation.message_count": message_count,
    }

    instruments = _instruments
    if instruments is not None:
        instruments.conversation_messages.record(
            message_count,
            _model_attributes(provider, model),
        )

    with trace_operation(
        f"chat {model}",
        span_attributes,
        kind=SpanKind.CLIENT,
    ) as span:
        try:
            yield span
        except BaseException as error:
            error_type = type(error).__name__
            raise
        finally:
            instruments = _instruments
            if instruments is not None:
                metric_attributes = _model_attributes(provider, model)
                metric_attributes["gen_ai.operation.name"] = "chat"

                if error_type is not None:
                    metric_attributes["error.type"] = error_type

                instruments.gen_ai_operation_duration.record(
                    perf_counter() - started_at,
                    metric_attributes,
                )


def record_gen_ai_response(
    *,
    provider: str,
    request_model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    response_model: str | None = None,
    response_id: str | None = None,
    finish_reason: str | None = None,
    truncated: bool = False,
    truncation_reason: str = "unknown",
) -> None:
    """Record model response metadata without recording response content."""

    span = trace.get_current_span()

    if span.is_recording():
        if response_model:
            span.set_attribute("gen_ai.response.model", response_model)

        if response_id:
            span.set_attribute("gen_ai.response.id", response_id[:256])

        if finish_reason:
            span.set_attribute(
                "gen_ai.response.finish_reasons",
                [finish_reason],
            )

        if type(input_tokens) is int and input_tokens >= 0:
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)

        if type(output_tokens) is int and output_tokens >= 0:
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)

        if truncated:
            span.set_attribute("kio1.response.truncated", True)

    instruments = _instruments
    if instruments is None:
        return

    metric_attributes = _model_attributes(provider, request_model)

    if response_model:
        metric_attributes["gen_ai.response.model"] = response_model

    if type(input_tokens) is int and input_tokens >= 0:
        input_attributes = dict(metric_attributes)
        input_attributes["gen_ai.token.type"] = "input"
        instruments.gen_ai_token_usage.record(
            input_tokens,
            input_attributes,
        )
        # Contract mandatory metric kio_llm_token_count (tag: direction).
        instruments.llm_token_count.add(input_tokens, {"direction": "input"})

    if type(output_tokens) is int and output_tokens >= 0:
        output_attributes = dict(metric_attributes)
        output_attributes["gen_ai.token.type"] = "output"
        instruments.gen_ai_token_usage.record(
            output_tokens,
            output_attributes,
        )
        instruments.llm_token_count.add(output_tokens, {"direction": "output"})

    total_tokens = 0
    if type(input_tokens) is int and input_tokens >= 0:
        total_tokens += input_tokens
    if type(output_tokens) is int and output_tokens >= 0:
        total_tokens += output_tokens

    if total_tokens > 0:
        # Contract mandatory metric kio_llm_cost_usd. This is an estimate
        # from a fixed per-provider coefficient (_COST_PER_1K_TOKENS_USD),
        # not real provider billing data.
        cost_usd = (
            total_tokens / 1000
        ) * _COST_PER_1K_TOKENS_USD.get(provider, 0.0)
        if cost_usd > 0:
            instruments.llm_cost_usd.add(cost_usd)

    if truncated:
        normalized_reason = (
            truncation_reason
            if truncation_reason in _ALLOWED_TRUNCATION_REASONS
            else "unknown"
        )
        truncation_attributes = dict(metric_attributes)
        truncation_attributes["kio1.truncation.reason"] = normalized_reason
        instruments.response_truncations.add(
            1,
            truncation_attributes,
        )


def record_session_started(*, provider: str, model: str) -> None:
    """Increment the session-started counter."""

    instruments = _instruments
    if instruments is not None:
        instruments.sessions_started.add(
            1,
            _model_attributes(provider, model),
        )
        # Contract mandatory metric kio_session_active_count. Best-effort:
        # if the process is killed before record_session_completed() runs,
        # this count will not be decremented until telemetry re-initializes.
        instruments.session_active_count.add(1)


def record_session_completed(
    *,
    provider: str,
    model: str,
    success: bool,
) -> None:
    """Increment the session-completed counter."""

    instruments = _instruments
    if instruments is not None:
        metric_attributes = _model_attributes(provider, model)
        metric_attributes["kio1.status"] = "success" if success else "error"
        instruments.sessions_completed.add(1, metric_attributes)
        instruments.session_active_count.add(-1)


def record_format_fallback() -> None:
    """Record use of the Python-literal response-format fallback."""

    instruments = _instruments
    if instruments is not None:
        instruments.format_fallbacks.add(1)

    span = trace.get_current_span()
    if span.is_recording():
        span.add_event("kio1.response.format_fallback")


def record_workflow_plan(
    *,
    workflow_id: str | None,
    execution_mode: str,
    step_count: int,
) -> None:
    """Record the structure of a successfully parsed workflow plan."""

    normalized_mode = (
        execution_mode if execution_mode in _ALLOWED_EXECUTION_MODES else "unknown"
    )

    instruments = _instruments
    if instruments is not None:
        metric_attributes: dict[str, AttributeValue] = {
            "kio1.workflow.execution_mode": normalized_mode,
        }
        instruments.workflow_plans.add(1, metric_attributes)
        instruments.workflow_step_count.record(
            step_count,
            metric_attributes,
        )

    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute(
            "kio1.workflow.execution_mode",
            normalized_mode,
        )
        span.set_attribute("kio1.workflow.step_count", step_count)

        if workflow_id:
            span.set_attribute("kio1.workflow.id", workflow_id[:128])
