"""Observability bootstrap: Microsoft OpenTelemetry distro (Agent 365) + local Jaeger via OTLP.

Must be imported BEFORE langchain/langgraph so auto-instrumentation can patch them.
"""
import atexit
import logging
import os
import threading
import time

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

from microsoft.opentelemetry import use_microsoft_opentelemetry

from agent.config import settings

log = logging.getLogger("agent.telemetry")

# The exporter calls the resolver as (agent_id, tenant_id). We keep the most recent
# OBO token per (agent, tenant); for this demo one presenter drives one session at a time.
_tokens: dict[tuple[str, str], tuple[str, float]] = {}
_lock = threading.Lock()


def cache_observability_token(agent_id: str, tenant_id: str, token: str) -> None:
    from agent.auth import jwt_expiry

    with _lock:
        _tokens[(agent_id, tenant_id)] = (token, jwt_expiry(token))


def _token_resolver(agent_id: str, tenant_id: str) -> str | None:
    with _lock:
        tok, exp = _tokens.get((agent_id, tenant_id), (None, 0.0))
    if not tok:
        log.warning("No cached Agent 365 observability token for agent=%s tenant=%s", agent_id, tenant_id)
        return None
    if exp < time.time() + 30:
        # Returning an expired token only produces HTTP 401 retry loops; the next signed-in
        # chat turn caches a fresh token, which durable replay then uses.
        log.warning("Cached Agent 365 observability token expired for agent=%s; waiting for next sign-in", agent_id)
        return None
    return tok


def init_telemetry() -> None:
    if settings.a365_verbose:
        # DEBUG includes export attempts and HTTP responses. An "Exporting" line alone
        # does not confirm delivery or downstream Purview content processing.
        logging.getLogger("microsoft.opentelemetry").setLevel(logging.DEBUG)
        log.info("A365_VERBOSE=true - Agent 365 exporter will log every export at DEBUG")

    processors = []
    if settings.otlp_local_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        processors.append(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_local_endpoint)))
        log.info("Local OTLP export -> %s", settings.otlp_local_endpoint)
    if settings.console_exporter:
        processors.append(SimpleSpanProcessor(ConsoleSpanExporter()))

    a365_export = settings.a365_exporter and settings.auth_enabled
    # SDK 1.3.9 ORs the kwarg with this env var; False alone cannot disable export.
    os.environ["ENABLE_A365_OBSERVABILITY_EXPORTER"] = str(a365_export).lower()
    use_microsoft_opentelemetry(
        resource=Resource.create({
            "service.name": settings.agent_name,
            "service.namespace": "agent365.demo",
        }),
        enable_a365=True,
        a365_enable_observability_exporter=a365_export,
        a365_use_s2s_endpoint=False,  # OBO -> /observability/... route
        a365_token_resolver=_token_resolver,
        span_processors=processors,
        # Agent 365 scopes are emitted explicitly (agent/a365_callbacks.py) for a clean
        # invoke_agent -> chat -> execute_tool tree; generic GenAI auto-instrumentation off.
        instrumentation_options={
            lib: {"enabled": False}
            for lib in ("langchain", "openai", "openai_agents", "semantic_kernel", "agent_framework")
        },
        disable_metrics=True,
        disable_logging=True,
    )
    log.info("Agent 365 exporter enabled: %s", a365_export)
    if not a365_export:
        reasons = []
        if not settings.a365_exporter:
            reasons.append("ENABLE_A365_OBSERVABILITY_EXPORTER=false")
        if not settings.auth_enabled:
            reasons.append("AUTH_ENABLED=false")
        log.warning(
            "\n"
            "  ==================================================================\n"
            "   NO TELEMETRY IS BEING SENT TO MICROSOFT 365.\n"
            "   Traces go to local Jaeger ONLY. Defender, Purview and the M365\n"
            "   admin center will stay EMPTY no matter how long you wait.\n"
            "   Reason: %s\n"
            "   Fix:    .\\scripts\\Configure-Entra.ps1   (needs Global Admin)\n"
            "  ==================================================================",
            " and ".join(reasons),
        )

    def _shutdown():
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()

    atexit.register(_shutdown)
