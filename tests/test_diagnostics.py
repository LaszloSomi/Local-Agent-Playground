"""Diagnostics tab: CA assignment evaluation and the /api/diagnostics contract (claims only, never raw tokens)."""
import base64
import json
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

import _fixture_env  # noqa: F401  (must precede agent imports)
from fastapi.testclient import TestClient

from agent import app as app_module
from agent import diagnostics
from agent.config import settings


def fake_jwt(**claims) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc({'exp': time.time() + 3600, **claims})}.SIGNATURE-SECRET"


def policy(name, state="enabled", **cond):
    return {"id": name, "displayName": name, "state": state, "conditions": cond,
            "grantControls": {"operator": "OR", "builtInControls": ["block"]}}


class EvaluateCaTests(unittest.TestCase):
    def setUp(self):
        self.agent = "agent-id"
        patcher = patch.object(diagnostics, "settings",
                               replace(settings, agent_id=self.agent, blueprint_id="bp-id", web_client_id="wc-id"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def evaluate(self, *policies, groups=()):
        return {r["name"]: r for r in diagnostics.evaluate_ca(list(policies), "user-1", set(groups), set())}

    def test_agent_identity_targeting(self):
        r = self.evaluate(
            policy("all-agents", applications={"includeApplications": ["All"]},
                   clientApplications={"includeAgentIdServicePrincipals": ["All"]}, users={"includeUsers": ["None"]}),
            policy("other-agent", applications={"includeApplications": ["AllAgentIdResources"]},
                   clientApplications={"includeAgentIdServicePrincipals": ["someone-else"]}),
            policy("excluded", applications={"includeApplications": ["All"]},
                   clientApplications={"includeAgentIdServicePrincipals": ["All"],
                                       "excludeServicePrincipals": [self.agent]}))
        self.assertEqual(r["all-agents"]["targets"], ["agent identity"])
        self.assertFalse(r["other-agent"]["applies"])
        self.assertFalse(r["excluded"]["applies"])

    def test_user_sign_in_targeting(self):
        r = self.evaluate(
            policy("mfa-all", applications={"includeApplications": ["All"]}, users={"includeUsers": ["All"]}),
            policy("group", applications={"includeApplications": ["bp-id"]}, users={"includeGroups": ["g1"]}),
            policy("excluded", applications={"includeApplications": ["All"]},
                   users={"includeUsers": ["All"], "excludeUsers": ["user-1"]}),
            policy("other-app", applications={"includeApplications": ["Office365"]}, users={"includeUsers": ["All"]}),
            groups=["g1"])
        self.assertEqual(r["mfa-all"]["targets"], ["user sign-in"])
        self.assertTrue(r["group"]["applies"])
        self.assertFalse(r["excluded"]["applies"])
        self.assertFalse(r["other-app"]["applies"])
        self.assertEqual(list(r)[:2], ["group", "mfa-all"])  # applicable first

    def test_agent_user_policies_not_applicable(self):
        r = self.evaluate(policy("agent-users", "disabled", applications={"includeApplications": ["All"]},
                                 users={"includeUsers": ["None"]}, agents={"includeAgentUsers": ["All"]}))
        self.assertFalse(r["agent-users"]["applies"])
        self.assertIn("no agent user", r["agent-users"]["reasons"][0])


class DiagnosticsEndpointTests(unittest.TestCase):
    def test_returns_claims_not_tokens(self):
        user_tok = fake_jwt(aud="api://bp", upn="alice@contoso.com", scp="access_agent_as_user")
        obo_tok = fake_jwt(aud="https://graph.microsoft.com", scp="Content.Process.User")
        app_module._SESSIONS.clear()
        client = TestClient(app_module.app)
        client.get("/api/me")
        state = next(iter(app_module._SESSIONS.values()))
        state["user"] = {"oid": "user-1", "upn": "alice@contoso.com", "name": "Alice"}
        on = replace(settings, auth_enabled=True, a365_exporter=True, purview_enabled=True, mcp_scope="")
        with patch.object(app_module, "settings", on), patch.object(diagnostics, "settings", on), \
             patch("agent.auth.fresh_user_token", return_value=user_tok), \
             patch("agent.auth._blueprint_t1", return_value=obo_tok), \
             patch("agent.auth.agent_obo_token", return_value=obo_tok), \
             patch("agent.diagnostics.purview_info", return_value={"enabled": True, "scopes": []}), \
             patch("agent.diagnostics.directory_info", return_value={"enabled": False, "note": "off"}):
            r = client.get("/api/diagnostics")
        self.assertEqual(r.status_code, 200)
        body = r.text
        self.assertNotIn("SIGNATURE-SECRET", body)
        self.assertNotIn(user_tok, body)
        tokens = {t["name"]: t for t in r.json()["tokens"]}
        self.assertEqual(tokens["User sign-in token (Tc)"]["keyClaims"]["upn"], "alice@contoso.com")
        self.assertEqual(tokens["Purview SDK token"]["status"], "ok")
        self.assertEqual(tokens["Observability token"]["status"], "ok")
        self.assertEqual(tokens["MCP token"]["status"], "not-configured")

    def test_requires_sign_in(self):
        with patch.object(app_module, "settings", replace(settings, auth_enabled=True)):
            r = TestClient(app_module.app).get("/api/diagnostics")
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
