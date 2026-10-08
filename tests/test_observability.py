"""Local-only instrumentation and SDK export-contract regression checks."""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import _fixture_env  # noqa: F401  (must precede agent imports)
from agent import verify_spans
from agent import app as app_module
from agent.config import settings
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from microsoft.opentelemetry.a365.core.exporters.agent365_exporter import _Agent365Exporter
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def simulated_turn(prompt, history, callbacks):
    cb = callbacks[0]
    run_id = uuid4()
    cb.on_chat_model_start({}, [[HumanMessage(prompt)]], run_id=run_id)
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(
        content="", tool_calls=[{"name": "get_weather", "args": {"place": "Toronto"}, "id": "call-1"}],
    ))]]), run_id=run_id)
    tool_id = uuid4()
    cb.on_tool_start({"name": "get_weather"}, '{"place":"Toronto"}', run_id=tool_id)
    cb.on_tool_end('{"temperature_c":20}', run_id=tool_id)
    run_id = uuid4()
    cb.on_chat_model_start({}, [[HumanMessage(prompt)]], run_id=run_id)
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="Toronto is 20 C."))]]),
                  run_id=run_id)
    return {"text": "Toronto is 20 C.", "tool_calls": []}


class ObservabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.memory = InMemorySpanExporter()
        trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(cls.memory))

    def setUp(self):
        self.memory.clear()

    def run_turn(self):
        with patch.object(app_module, "run_agent", side_effect=simulated_turn):
            with TestClient(app_module.app, client=("127.0.0.1", 50000)) as client:
                response = client.post("/api/chat", json={"message": "weather in Toronto"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "Toronto is 20 C.")
        return self.memory.get_finished_spans()

    def test_scopes_content_endpoints_and_correlation(self):
        spans = self.run_turn()
        self.assertEqual(len(spans), 4)
        self.assertTrue(verify_spans._audit(spans))
        self.assertTrue(verify_spans._audit_correlation(spans))
        root = next(s for s in spans if s.parent is None)
        self.assertEqual(json.loads(root.attributes["gen_ai.input.messages"])[0]["parts"][0]["content"],
                         "weather in Toronto")
        self.assertEqual(json.loads(root.attributes["gen_ai.output.messages"])[0]["parts"][0]["content"],
                         "Toronto is 20 C.")
        for span in spans:
            self.assertEqual(span.attributes["client.address"], "127.0.0.1")
            self.assertTrue(span.attributes["server.address"])
            self.assertTrue(span.attributes["server.port"])

    def test_actual_sdk_http_payload_keeps_content(self):
        spans = self.run_turn()
        exporter = _Agent365Exporter(token_resolver=lambda *_: "test-only", enable_durable_delivery=False)
        try:
            response = Mock(status_code=200, headers={}, text='{"partialSuccess":{"rejectedSpans":0}}')
            with patch.object(exporter._session, "post", return_value=response) as post:
                self.assertEqual(exporter.export(spans), SpanExportResult.SUCCESS)
            self.assertEqual(post.call_count, 1)
            url = post.call_args.args[0]
            self.assertIn(f"/observability/tenants/{settings.tenant_id}/otlp/agents/{settings.agent_id}/", url)
            body = json.loads(post.call_args.kwargs["data"])
            sent = [s for r in body["resourceSpans"] for scope in r["scopeSpans"] for s in scope["spans"]]
            self.assertEqual(len(sent), 4)
            root = next(s for s in sent if s["attributes"]["gen_ai.operation.name"] == "invoke_agent")
            self.assertIn("weather in Toronto", root["attributes"]["gen_ai.input.messages"])
            self.assertIn("Toronto is 20 C.", root["attributes"]["gen_ai.output.messages"])
            self.assertEqual(len({s["attributes"]["microsoft.session.id"] for s in sent}), 1)
        finally:
            exporter.shutdown()

    def test_local_check_never_posts_telemetry(self):
        # Local Jaeger (OTLP_LOCAL_ENDPOINT) may export; nothing may reach the Agent 365 cloud service.
        ok = Mock(status_code=200, ok=True, headers={}, text="", content=b"")
        with patch("requests.sessions.Session.post", return_value=ok) as post:
            self.run_turn()
            self.assertTrue(trace.get_tracer_provider().force_flush())
        urls = [str(c.kwargs.get("url") or (c.args[0] if c.args else "")) for c in post.call_args_list]
        self.assertEqual([u for u in urls if "agent365" in u or "observability" in u], [])

    def test_auth_disabled_overrides_exporter_environment(self):
        env = dict(os.environ, AUTH_ENABLED="false", ENABLE_A365_OBSERVABILITY_EXPORTER="true",
                   OTLP_LOCAL_ENDPOINT="", OTEL_CONSOLE_EXPORTER="false", A365_VERBOSE="false")
        result = subprocess.run([sys.executable, "-c", """
import os
from unittest.mock import patch
from agent.telemetry import init_telemetry
from agent.config import settings
from opentelemetry import trace
from microsoft.opentelemetry.a365.core import AgentDetails, InvokeAgentScope, InvokeAgentScopeDetails, Request
assert settings.a365_exporter and not settings.auth_enabled
with patch('requests.sessions.Session.post', side_effect=AssertionError('Unexpected HTTP')) as post:
    init_telemetry()
    assert os.environ['ENABLE_A365_OBSERVABILITY_EXPORTER'] == 'false'
    with InvokeAgentScope.start(Request(content='offline', conversation_id='test'),
                                InvokeAgentScopeDetails(),
                                AgentDetails(agent_id=settings.agent_id, tenant_id=settings.tenant_id)) as scope:
        scope.record_output_messages(['offline'])
    assert trace.get_tracer_provider().force_flush()
    post.assert_not_called()
"""], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_audit_rejects_empty_missing_and_malformed_spans(self):
        spans = self.run_turn()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(verify_spans._audit([]))
            self.assertFalse(verify_spans._audit_correlation([]))
            attrs = dict(spans[0].attributes)
            attrs["server.address"] = ""
            self.assertFalse(verify_spans._audit([SimpleNamespace(attributes=attrs, name="bad")]))
            attrs = dict(spans[0].attributes)
            attrs["gen_ai.input.messages"] = "not JSON"
            self.assertFalse(verify_spans._audit([SimpleNamespace(attributes=attrs, name="bad")]))
            child = spans[0]
            attrs = dict(child.attributes)
            attrs["microsoft.session.id"] = "different-session"
            invalid = SimpleNamespace(attributes=attrs, name=child.name, context=child.context, parent=child.parent)
            self.assertFalse(verify_spans._audit_correlation([invalid, *spans[1:]]))

    def test_verify_command_fails_on_http_error(self):
        with patch.object(verify_spans, "TestClient") as client, contextlib.redirect_stdout(io.StringIO()):
            client.return_value.__enter__.return_value.post.return_value = Mock(
                status_code=401, json=lambda: {"error": "not_signed_in"},
            )
            self.assertEqual(verify_spans.main(), 1)


def _jwt(exp: float) -> str:
    import base64

    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp)})}.sig"


class TokenLifetimeTests(unittest.TestCase):
    def test_resolver_never_returns_expired_token(self):
        import time

        from agent import telemetry

        telemetry.cache_observability_token("a", "t", _jwt(time.time() - 10))
        self.assertIsNone(telemetry._token_resolver("a", "t"))
        fresh = _jwt(time.time() + 3600)
        telemetry.cache_observability_token("a", "t", fresh)
        self.assertEqual(telemetry._token_resolver("a", "t"), fresh)

    def test_expired_user_token_is_not_exchanged(self):
        import time

        from agent import auth

        with patch.object(auth, "httpx") as http:
            with self.assertRaises(auth.ReauthRequired):
                auth.observability_token_for_user(str(uuid4()), _jwt(time.time() - 10))
            http.post.assert_not_called()

    def test_fresh_user_token_refreshes_silently_for_session_account(self):
        from agent import auth

        client = Mock()
        client.get_accounts.return_value = [{"home_account_id": "other.t"}, {"home_account_id": "u.t"}]
        client.acquire_token_silent.return_value = {"access_token": "new-tc"}
        state = {"home_account_id": "u.t", "token_cache": None, "access_token": "old-tc"}
        with patch.object(auth, "web_client", return_value=client):
            self.assertEqual(auth.fresh_user_token(state), "new-tc")
        self.assertEqual(client.acquire_token_silent.call_args.kwargs["account"], {"home_account_id": "u.t"})
        self.assertEqual(state["access_token"], "new-tc")
        client.acquire_token_silent.return_value = None
        with patch.object(auth, "web_client", return_value=client), self.assertRaises(auth.ReauthRequired):
            auth.fresh_user_token(state)

    def test_chat_requires_reauth_when_refresh_fails(self):
        from dataclasses import replace

        from agent import auth

        sid_state = {"history": [], "conversation_id": "c", "user": {"oid": "u", "upn": "u@x", "name": "U"}}
        on = replace(settings, auth_enabled=True, a365_exporter=True)
        with patch.object(app_module, "settings", on), \
                patch.object(app_module, "_state", return_value=sid_state), \
                patch.object(auth, "fresh_user_token", side_effect=auth.ReauthRequired("expired")), \
                patch.object(app_module, "run_agent") as run:
            with TestClient(app_module.app, client=("127.0.0.1", 50000)) as client:
                r = client.post("/api/chat", json={"message": "weather in Toronto"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"], "session_expired")
        run.assert_not_called()
        self.assertNotIn("user", sid_state)


def _resp(status=200, body=None, headers=None):
    return Mock(status_code=status, headers=headers or {}, content=b"x" if body is not None else b"",
                json=lambda: body or {}, text=json.dumps(body or {}))


class PurviewTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace

        from agent import purview

        self.purview = purview
        purview._scope_cache.clear()
        self.on = replace(settings, auth_enabled=True, purview_enabled=True, a365_exporter=False,
                          purview_app_location="agent-app-id", purview_fail_closed=False,
                          purview_log_when_unscoped=True)
        self.ctx = purview.TurnContext(user_oid="u1", token="t", conversation_id="conv", sequence=2,
                                       client_ip="10.0.0.5")

    def _scopes(self, mode, activities="uploadText,downloadText"):
        return _resp(200, {"value": [{"activities": activities, "executionMode": mode, "locations": [],
                                      "policyActions": []}]}, {"ETag": '"e1"'})

    def test_inline_block_sends_text_with_agent_info_and_etag(self):
        block = _resp(200, {"protectionScopeState": "notModified", "processingErrors": [], "policyActions": [
            {"action": "restrictAccess", "restrictionAction": "block"}]})
        with patch.object(self.purview, "settings", self.on), \
                patch.object(self.purview.httpx, "post", side_effect=[self._scopes("evaluateInline"), block]) as post:
            d = self.purview.evaluate(self.ctx, "uploadText", "weather in Dallas")
        self.assertFalse(d.allowed)
        self.assertEqual(d.status, "inline-blocked")
        compute, process = post.call_args_list
        self.assertTrue(compute.args[0].endswith("/me/dataSecurityAndGovernance/protectionScopes/compute"))
        self.assertEqual(compute.kwargs["json"]["locations"][0]["value"], "agent-app-id")
        self.assertTrue(process.args[0].endswith("/processContent"))
        self.assertEqual(process.kwargs["headers"]["If-None-Match"], '"e1"')
        body = process.kwargs["json"]["contentToProcess"]
        entry = body["contentEntries"][0]
        self.assertEqual(entry["content"]["data"], "weather in Dallas")
        self.assertEqual((entry["correlationId"], entry["sequenceNumber"]), ("conv", 2))
        self.assertEqual(entry["agents"][0]["identifier"], settings.agent_id)
        self.assertEqual(entry["agents"][0]["blueprintId"], settings.blueprint_id)
        self.assertEqual(body["activityMetadata"]["activity"], "uploadText")
        self.assertEqual(body["deviceMetadata"]["ipAddress"], "10.0.0.5")

    def test_inline_allow_and_modified_state_recomputes_scopes(self):
        ok = _resp(200, {"protectionScopeState": "modified", "policyActions": []})
        with patch.object(self.purview, "settings", self.on), \
                patch.object(self.purview.httpx, "post",
                             side_effect=[self._scopes("evaluateInline"), ok, self._scopes("evaluateInline"), ok]) as post:
            self.assertTrue(self.purview.evaluate(self.ctx, "uploadText", "weather in SF").allowed)
            self.assertNotIn("u1", self.purview._scope_cache)
            self.purview.evaluate(self.ctx, "uploadText", "again")
        self.assertEqual(post.call_count, 4)

    def test_unscoped_logs_metadata_only_activity(self):
        with patch.object(self.purview, "settings", self.on), \
                patch.object(self.purview, "_background", side_effect=lambda fn, *a: fn(*a)), \
                patch.object(self.purview.httpx, "post",
                             side_effect=[_resp(200, {"value": []}), _resp(201, {"id": "x"})]) as post:
            d = self.purview.evaluate(self.ctx, "downloadText", "reply")
        self.assertTrue(d.allowed)
        self.assertEqual(d.status, "no-scope-activity-logged")
        self.assertTrue(post.call_args.args[0].endswith("/activities/contentActivities"))
        self.assertNotIn("content", post.call_args.kwargs["json"]["contentToProcess"]["contentEntries"][0])

    def test_offline_scope_does_not_block(self):
        with patch.object(self.purview, "settings", self.on), \
                patch.object(self.purview, "_background") as bg, \
                patch.object(self.purview.httpx, "post", return_value=self._scopes("evaluateOffline", "uploadText")):
            d = self.purview.evaluate(self.ctx, "uploadText", "hi")
        self.assertEqual((d.allowed, d.status), (True, "offline-sent"))
        self.assertIs(bg.call_args.args[0], self.purview.process_content)

    def test_failure_is_open_by_default_and_closed_when_configured(self):
        from dataclasses import replace

        err = _resp(403, {"error": "Forbidden"})
        with patch.object(self.purview, "settings", self.on), patch.object(self.purview.httpx, "post", return_value=err):
            self.assertTrue(self.purview.evaluate(self.ctx, "uploadText", "hi").allowed)
        closed = replace(self.on, purview_fail_closed=True)
        with patch.object(self.purview, "settings", closed), patch.object(self.purview.httpx, "post", return_value=err):
            self.assertFalse(self.purview.evaluate(self.ctx, "uploadText", "hi").allowed)

    def test_chat_blocks_prompt_before_llm(self):
        from agent import auth, guard

        sid_state = {"history": [], "conversation_id": "c", "user": {"oid": "u", "upn": "u@x", "name": "U"}}
        blocked = self.purview.Decision(allowed=False, status="inline-blocked", reason=self.purview.BLOCK_MESSAGE)
        with patch.object(app_module, "settings", self.on), patch.object(guard, "settings", self.on), \
                patch.object(app_module, "_state", return_value=sid_state), \
                patch.object(auth, "fresh_user_token", return_value="tc"), \
                patch.object(auth, "purview_token_for_user", return_value="graph"), \
                patch.object(self.purview, "evaluate", return_value=blocked) as ev, \
                patch.object(app_module, "run_agent") as run:
            with TestClient(app_module.app, client=("127.0.0.1", 50000)) as client:
                r = client.post("/api/chat", json={"message": "weather in Dallas"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["blocked"])
        self.assertEqual(r.json()["reply"], self.purview.BLOCK_MESSAGE)
        self.assertEqual(r.json()["purview"], "prompt:inline-blocked")
        self.assertEqual(ev.call_args.args[1:], ("uploadText", "weather in Dallas"))
        run.assert_not_called()

    def test_chat_blocks_response(self):
        from agent import auth, guard

        sid_state = {"history": [], "conversation_id": "c", "user": {"oid": "u", "upn": "u@x", "name": "U"}}
        decisions = [self.purview.Decision(status="inline-allowed"),
                     self.purview.Decision(allowed=False, status="inline-blocked", reason="nope")]
        with patch.object(app_module, "settings", self.on), patch.object(guard, "settings", self.on), \
                patch.object(app_module, "_state", return_value=sid_state), \
                patch.object(auth, "fresh_user_token", return_value="tc"), \
                patch.object(auth, "purview_token_for_user", return_value="graph"), \
                patch.object(self.purview, "evaluate", side_effect=decisions) as ev, \
                patch.object(app_module, "run_agent", side_effect=simulated_turn):
            with TestClient(app_module.app, client=("127.0.0.1", 50000)) as client:
                r = client.post("/api/chat", json={"message": "weather in Toronto"})
        self.assertEqual((r.json()["reply"], r.json()["blocked"]), ("nope", True))
        self.assertEqual(r.json()["purview"], "prompt:inline-allowed · response:inline-blocked")
        self.assertEqual(ev.call_args.args[1:], ("downloadText", "Toronto is 20 C."))


if __name__ == "__main__":
    unittest.main()
