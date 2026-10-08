"""LangChain callback handler that emits Agent 365 InferenceScope / ExecuteToolScope spans.

We use explicit scopes (instead of generic LangChain auto-instrumentation) so each run is a
clean Agent 365 tree:  invoke_agent -> chat -> execute_tool -> chat
"""
import json
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from microsoft.opentelemetry.a365.core import (
    AgentDetails,
    ExecuteToolScope,
    InferenceCallDetails,
    InferenceOperationType,
    InferenceScope,
    Request,
    ServiceEndpoint,
    SpanDetails,
    ToolCallDetails,
    ToolType,
    UserDetails,
)

from agent.config import settings


class A365ScopeCallback(BaseCallbackHandler):
    def __init__(self, request: Request, agent_details: AgentDetails, user: UserDetails, parent_context):
        self.request = request
        self.agent_details = agent_details
        self.user = user
        self.parent = SpanDetails(parent_context=parent_context)
        self._open: dict[UUID, Any] = {}

    # ── LLM ──────────────────────────────────────────────────────────────────
    def on_chat_model_start(self, serialized, messages, *, run_id: UUID, **kwargs):
        url = urlsplit(settings.github_endpoint if settings.llm_provider == "github" else settings.ollama_base_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("The model endpoint must be an absolute HTTP(S) URL")
        endpoint = ServiceEndpoint(
            hostname=url.hostname,
            port=url.port or (443 if url.scheme == "https" else 80),
        )
        scope = InferenceScope.start(
            self.request,
            InferenceCallDetails(
                operationName=InferenceOperationType.CHAT,
                model=settings.model_label,
                providerName=settings.provider_label,
                endpoint=endpoint,
            ),
            self.agent_details,
            self.user,
            self.parent,
        )
        # SDK quirk: InferenceOperationType.CHAT is "Chat", but the A365 exporter only accepts
        # lowercase "chat" and silently drops anything else.
        scope.set_tag_maybe("gen_ai.operation.name", "chat")
        last = messages[0][-1] if messages and messages[0] else None
        if last is not None and isinstance(last.content, str) and last.content:
            scope.record_input_messages([last.content])
        self._open[run_id] = scope

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs):
        scope = self._open.pop(run_id, None)
        if not scope:
            return
        try:
            gen = response.generations[0][0] if response.generations and response.generations[0] else None
            msg = getattr(gen, "message", None)
            text = (getattr(msg, "content", None) or getattr(gen, "text", "")) if gen else ""
            tool_calls = getattr(msg, "tool_calls", None) or []
            out = text or (f"[tool_call] {', '.join(tc['name'] for tc in tool_calls)}" if tool_calls else "")
            if out:
                scope.record_output_messages([out])
            usage = getattr(msg, "usage_metadata", None) or {}
            if usage.get("input_tokens") is not None:
                scope.record_input_tokens(int(usage["input_tokens"]))
            if usage.get("output_tokens") is not None:
                scope.record_output_tokens(int(usage["output_tokens"]))
            scope.record_finish_reasons(["tool_calls" if tool_calls else "stop"])
        finally:
            scope.dispose()

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs):
        scope = self._open.pop(run_id, None)
        if scope:
            scope.record_error(error)
            scope.dispose()

    # ── Tools ────────────────────────────────────────────────────────────────
    def on_tool_start(self, serialized, input_str, *, run_id: UUID, inputs=None, **kwargs):
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        scope = ExecuteToolScope.start(
            self.request,
            ToolCallDetails(
                tool_name=name,
                arguments=json.dumps(inputs) if inputs else input_str,
                tool_call_id=str(run_id),
                description=(serialized or {}).get("description"),
                tool_type=ToolType.FUNCTION.value,
                # These function tools execute inside the agent, even when they call HTTP APIs.
                endpoint=ServiceEndpoint(hostname=settings.app_host, port=settings.app_port),
            ),
            self.agent_details,
            self.user,
            self.parent,
        )
        # The SDK omits port 443, but the canonical ingestion contract requires it.
        scope.set_tag_maybe("server.port", str(settings.app_port))
        self._open[run_id] = scope

    def on_tool_end(self, output, *, run_id: UUID, **kwargs):
        scope = self._open.pop(run_id, None)
        if scope:
            content = getattr(output, "content", output)
            scope.record_response(content if isinstance(content, str) else str(content))
            scope.dispose()

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs):
        scope = self._open.pop(run_id, None)
        if scope:
            scope.record_error(error)
            scope.dispose()
