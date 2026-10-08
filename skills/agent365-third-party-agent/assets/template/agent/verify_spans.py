"""Offline check: which spans from one agent turn would the Agent 365 exporter actually send?

Runs a turn with an in-memory span exporter, then applies the SDK's own
filter_and_partition_by_identity() (the same filter the A365 exporter uses).
Usage:  .venv\\Scripts\\python -m agent.verify_spans
"""
import os
import json
import sys

os.environ.setdefault("AGENT365_TENANT_ID", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("AGENT365_AGENT_ID", "00000000-0000-0000-0000-000000000002")
os.environ.setdefault("AGENT365_BLUEPRINT_ID", "00000000-0000-0000-0000-000000000003")
os.environ["OTLP_LOCAL_ENDPOINT"] = ""
os.environ["ENABLE_A365_OBSERVABILITY_EXPORTER"] = "false"
os.environ["OTEL_CONSOLE_EXPORTER"] = "false"
# This is an offline span-shape check, not an auth check. Once AUTH_ENABLED=true the
# TestClient has no session cookie and /api/chat returns 401 before any span is created.
os.environ["AUTH_ENABLED"] = "false"

from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from microsoft.opentelemetry.a365.core.exporters.utils import filter_and_partition_by_identity  # noqa: E402

from agent.app import app  # noqa: E402


# Required attributes per scope, from
# https://learn.microsoft.com/microsoft-agent-365/developer/observability-attribute-reference
_COMMON_REQUIRED = {
    "microsoft.a365.agent.blueprint.id",
    "gen_ai.agent.id",
    "gen_ai.agent.name",
    "client.address",
    "server.address",
    "server.port",
    "microsoft.channel.name",
    "gen_ai.conversation.id",
    "gen_ai.operation.name",
    "microsoft.tenant.id",
}
REQUIRED_BY_OP = {
    "invoke_agent": _COMMON_REQUIRED
    | {"gen_ai.input.messages", "gen_ai.output.messages", "user.id"},
    "execute_tool": _COMMON_REQUIRED
    | {
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.id",
        "gen_ai.tool.call.result",
        "gen_ai.tool.name",
        "gen_ai.tool.type",
    },
    "chat": _COMMON_REQUIRED
    | {
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.provider.name",
        "gen_ai.request.model",
    },
}

def _audit(spans):
    print("\nRequired-attribute audit (Learn canonical attribute reference):")
    ok = bool(spans)
    for s in spans:
        attrs = s.attributes or {}
        op = str(attrs.get("gen_ai.operation.name") or "")
        required = REQUIRED_BY_OP.get(op)
        if not required:
            print(f"  MISS unknown operation: {op!r}")
            ok = False
            continue
        missing = sorted(k for k in required if attrs.get(k) is None or str(attrs[k]).strip() == "")
        status = "OK  " if not missing else "MISS"
        if missing:
            ok = False
        print(f"  {status} {op:14} {s.name}")
        if missing:
            print(f"         missing: {', '.join(missing)}")
        for key in ("gen_ai.input.messages", "gen_ai.output.messages"):
            if key not in required:
                continue
            try:
                messages = json.loads(attrs.get(key, ""))
                valid = isinstance(messages, list) and bool(messages) and all(
                    isinstance(m, dict) and m.get("role") and isinstance(m.get("parts"), list) and m["parts"]
                    for m in messages
                )
            except (ValueError, TypeError):
                valid = False
            if not valid:
                print(f"         invalid message array: {key}")
                ok = False
    print("\nAll required attributes present:", ok)
    return ok


def _audit_correlation(spans):
    roots = [s for s in spans if s.parent is None]
    if len(roots) != 1 or (roots[0].attributes or {}).get("gen_ai.operation.name") != "invoke_agent":
        print("FAIL: expected exactly one root invoke_agent")
        return False
    root = roots[0]
    attrs = root.attributes or {}
    keys = ("gen_ai.conversation.id", "microsoft.session.id", "microsoft.channel.name",
            "gen_ai.agent.id", "microsoft.tenant.id", "microsoft.a365.agent.blueprint.id")
    ok = True
    for span in spans:
        values = span.attributes or {}
        matches = span.context.trace_id == root.context.trace_id and all(
            attrs.get(k) and values.get(k) == attrs[k] for k in keys
        )
        if span is not root:
            matches = matches and span.parent is not None and span.parent.span_id == root.context.span_id
        if not matches:
            print(f"FAIL: inconsistent trace/parent/identity/conversation/session/channel: {span.name}")
            ok = False
    print("Consistent run correlation:", ok)
    return ok


def main():
    mem = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(mem))
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        r = c.post("/api/chat", json={"message": "What's the weather in San Francisco?"})
        print("HTTP", r.status_code, r.json().get("reply"))
    spans = mem.get_finished_spans()
    groups = filter_and_partition_by_identity(spans)
    exported = [s for g in groups.values() for s in g]
    print(f"\nTotal spans: {len(spans)}   exportable to Agent 365: {len(exported)}")
    for s in spans:
        op = (s.attributes or {}).get("gen_ai.operation.name")
        attrs = s.attributes or {}
        content = "".join(
            c for c, k in (("i", "gen_ai.input.messages"), ("o", "gen_ai.output.messages")) if k in attrs
        ) or "-"
        print(f"  {'SEND' if s in exported else 'drop'}  {op!s:14} {content:2} {s.name}")
    roots = [s for s in exported if s.parent is None]
    print("\nRoot invoke_agent present:", any((s.attributes or {}).get("gen_ai.operation.name") == "invoke_agent" for s in roots))
    attributes_ok = _audit(spans)
    correlation_ok = _audit_correlation(exported)
    success = r.status_code == 200 and bool(spans) and len(exported) == len(spans)
    if not (success and attributes_ok and correlation_ok):
        print("FAIL: local span validation failed; no cloud delivery was tested.")
        return 1
    print("PASS: local span validation only; no cloud delivery was tested.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
