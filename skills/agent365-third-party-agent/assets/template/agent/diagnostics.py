"""Diagnostics tab: Entra ID sign-in, the agent's token chain, the agent identity and the policies that apply to it.

Two data sources, shown separately in the UI:
  * The agent's own tokens (user Tc, blueprint T1, OBO tokens for observability / Purview / MCP). Only decoded CLAIMS
    are returned, never a raw token.
  * Directory and policy data (agent identity, blueprint, inheritable permissions, grants, Conditional Access) read
    from Microsoft Graph with the Azure CLI session on this machine (`az login`), because the agent's delegated
    tokens intentionally can't read the directory. Turn off with DIAG_ADMIN_GRAPH=false.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent import auth, purview
from agent.config import settings

log = logging.getLogger("agent.diagnostics")

GRAPH = "https://graph.microsoft.com"
ROOT = Path(__file__).resolve().parent.parent
KNOWN_RESOURCES = {
    "00000003-0000-0000-c000-000000000000": "Microsoft Graph",
    "9b975845-388f-4429-889e-eab1ef63949c": "Agent 365 Observability (maven-prod)",
    "8578e004-a5c6-46e7-913e-12f58912df43": "Power Platform API",
}
KEY_CLAIMS = ("aud", "iss", "appid", "azp", "app_displayname", "idtyp", "oid", "sub", "upn", "preferred_username",
              "name", "tid", "scp", "roles", "amr", "acrs", "deviceid", "ipaddr", "iat", "nbf", "exp")


# ── Tokens ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def decode_claims(token: str) -> dict:
    """Unverified JWT payload (display only)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def token_summary(name: str, purpose: str, token: str | None = None, error: str | None = None,
                  status: str | None = None) -> dict:
    out = {"name": name, "purpose": purpose, "status": status or ("ok" if token else "error" if error else "absent")}
    if error:
        out["error"] = error
    if token:
        c = decode_claims(token)
        exp = c.get("exp")
        out.update({
            "fingerprint": "sha256:" + hashlib.sha256(token.encode()).hexdigest()[:16],
            "length": len(token),
            "expiresAt": _iso(exp),
            "minutesLeft": round((float(exp) - time.time()) / 60, 1) if exp else None,
            "keyClaims": {k: c[k] for k in KEY_CLAIMS if k in c}
                         | {k: v for k, v in c.items() if k.startswith("xms_")},
            "claims": c,
        })
        for k in ("iat", "nbf", "exp"):
            if k in out["keyClaims"]:
                out["keyClaims"][k] = f"{out['keyClaims'][k]} ({_iso(out['keyClaims'][k])})"
    return out


def _obo(name: str, purpose: str, oid: str, user_token: str, scope: str) -> dict:
    try:
        return token_summary(name, purpose, auth.agent_obo_token(oid, user_token, scope)) | {"scopeRequested": scope}
    except Exception as e:
        return token_summary(name, purpose, error=str(e)) | {"scopeRequested": scope}


def token_chain(state: dict, user: dict) -> tuple[list[dict], str | None]:
    """Returns (token summaries, user token). Each OBO call reuses the auth module cache, so this is cheap."""
    tokens: list[dict] = []
    try:
        user_token = auth.fresh_user_token(state)
        tokens.append(token_summary("User sign-in token (Tc)",
                                    f"Web client signs the user in; audience = blueprint ({settings.blueprint_scope})",
                                    user_token))
    except Exception as e:
        return [token_summary("User sign-in token (Tc)", "Web client → blueprint", error=str(e))], None
    try:
        tokens.append(token_summary("Blueprint exchange token (T1)",
                                    "Blueprint client credentials with fmi_path = agent identity (api://AzureADTokenExchange)",
                                    auth._blueprint_t1()))
    except Exception as e:
        tokens.append(token_summary("Blueprint exchange token (T1)", "Blueprint → agent identity", error=str(e)))
    oid = user["oid"]
    if settings.a365_exporter:
        tokens.append(_obo("Observability token", "Agent identity OBO → Agent 365 Observability exporter",
                           oid, user_token, auth.OBSERVABILITY_OBO_SCOPE))
    else:
        tokens.append(token_summary("Observability token", "Agent 365 exporter", status="disabled",
                                    error="ENABLE_A365_OBSERVABILITY_EXPORTER=false"))
    if settings.purview_enabled:
        tokens.append(_obo("Purview SDK token", "Agent identity OBO → Microsoft Graph Purview APIs (processContent)",
                           oid, user_token, auth.PURVIEW_OBO_SCOPE))
    else:
        tokens.append(token_summary("Purview SDK token", "Graph Purview APIs", status="disabled",
                                    error="PURVIEW_ENABLED=false"))
    if settings.mcp_scope:
        tokens.append(_obo("MCP token", "Agent identity OBO → MCP server", oid, user_token, settings.mcp_scope))
    else:
        tokens.append(token_summary("MCP token", "Agent identity OBO → MCP server", status="not-configured",
                                    error="This agent calls no MCP servers (set MCP_SCOPE to request one)."))
    return tokens, user_token


# ── Directory / policy data via the local Azure CLI session ────────────────────────────────────────────────────────

_admin_token: tuple[str, float] | None = None


def _az_graph_token() -> str:
    global _admin_token
    if _admin_token and _admin_token[1] > time.time() + 300:
        return _admin_token[0]
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        raise RuntimeError("Azure CLI not found; run 'az login' on this machine to show directory data")
    args = [az, "account", "get-access-token", "--resource-type", "ms-graph", "-o", "json"]
    if settings.tenant_id:
        args += ["--tenant", settings.tenant_id]
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(f"az account get-access-token failed: {r.stderr.strip()[:300]}")
    tok = json.loads(r.stdout)["accessToken"]
    _admin_token = (tok, auth.jwt_expiry(tok))
    return tok


class Graph:
    def __init__(self, token: str):
        self.client = httpx.Client(base_url=GRAPH, headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def get(self, path: str) -> dict:
        r = self.client.get(path)
        if r.status_code >= 400:
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text[:200]
            raise RuntimeError(f"HTTP {r.status_code}: {msg}")
        return r.json()

    def safe(self, path: str) -> dict:
        try:
            return self.get(path)
        except Exception as e:
            return {"_error": str(e)}


def _sp_names(g: Graph, ids: set[str], by: str = "id") -> dict[str, str]:
    out = {}
    for i in ids:
        if i in KNOWN_RESOURCES:
            out[i] = KNOWN_RESOURCES[i]
            continue
        path = f"/v1.0/servicePrincipals/{i}" if by == "id" else f"/v1.0/servicePrincipals(appId='{i}')"
        d = g.safe(path + "?$select=displayName,appId")
        out[i] = d.get("displayName", i)
    return out


def _control_summary(p: dict) -> str:
    gc = p.get("grantControls") or {}
    parts = list(gc.get("builtInControls") or [])
    if (gc.get("authenticationStrength") or {}).get("displayName"):
        parts.append(f"authStrength: {gc['authenticationStrength']['displayName']}")
    sc = {k: v for k, v in (p.get("sessionControls") or {}).items() if v and not k.startswith("@")}
    if sc:
        parts.append("session: " + ", ".join(sc))
    return f" {gc.get('operator', '')} ".join(parts) if parts else "none"


def _conditions_summary(c: dict) -> list[str]:
    out = []
    for k in ("locations", "platforms", "devices"):
        v = c.get(k)
        if v:
            out.append(f"{k}: " + json.dumps({x: y for x, y in v.items() if y and not x.startswith("@")}))
    for k in ("signInRiskLevels", "userRiskLevels", "servicePrincipalRiskLevels"):
        if c.get(k):
            out.append(f"{k}: {','.join(c[k])}")
    if c.get("agentIdRiskLevels"):
        out.append(f"agentIdRiskLevels: {c['agentIdRiskLevels']}")
    if c.get("clientAppTypes") and c["clientAppTypes"] != ["all"]:
        out.append("clientAppTypes: " + ",".join(c["clientAppTypes"]))
    f = (c.get("clientApplications") or {}).get("agentIdServicePrincipalFilter")
    if f:
        out.append(f"agent filter ({f.get('mode')}): {f.get('rule')}")
    return out


def evaluate_ca(policies: list[dict], user_oid: str, member_ids: set[str], role_ids: set[str]) -> list[dict]:
    """Static evaluation of CA *assignments* against this agent and the signed-in user. Device, location and risk
    conditions are only known at sign-in, so they're listed, not evaluated."""
    agent, bp, wc = settings.agent_id, settings.blueprint_id, settings.web_client_id
    results = []
    for p in policies:
        c = p.get("conditions") or {}
        apps = c.get("applications") or {}
        inc_apps, exc_apps = set(apps.get("includeApplications") or []), set(apps.get("excludeApplications") or [])
        users = c.get("users") or {}
        ca = c.get("clientApplications") or {}
        targets, reasons = [], []

        # 1. Agent identity as the client (OBO token requests for observability / Purview / MCP)
        inc_agents = set(ca.get("includeAgentIdServicePrincipals") or [])
        if ("All" in inc_agents or agent in inc_agents) and agent not in set(ca.get("excludeServicePrincipals") or []):
            if inc_apps & {"All", "AllAgentIdResources"} or inc_apps & {"00000003-0000-0000-c000-000000000000",
                                                                         "9b975845-388f-4429-889e-eab1ef63949c"}:
                targets.append("agent identity")
                reasons.append("includes " + ("all agent identities" if "All" in inc_agents else "this agent identity"))
                if ca.get("agentIdServicePrincipalFilter"):
                    reasons.append("subject to the custom-security-attribute filter")

        # 2. The user's sign-in to this agent (resource = blueprint, client = web client)
        inc_u, exc_u = set(users.get("includeUsers") or []), set(users.get("excludeUsers") or [])
        user_in = ("All" in inc_u or user_oid in inc_u or member_ids & set(users.get("includeGroups") or [])
                   or role_ids & set(users.get("includeRoles") or []))
        user_out = (user_oid in exc_u or member_ids & set(users.get("excludeGroups") or [])
                    or role_ids & set(users.get("excludeRoles") or []))
        app_in = bool(inc_apps & {"All", "AllAgentIdResources", bp, wc}) and not (exc_apps & {bp, wc})
        if user_in and not user_out and app_in:
            targets.append("user sign-in")
            reasons.append("includes the signed-in user and " +
                           ("all resources" if "All" in inc_apps else "agent resources" if "AllAgentIdResources" in inc_apps
                            else "this agent's apps"))
        elif user_in and user_out:
            reasons.append("user is excluded")

        # 3. Agent users (this agent has none: it's a standard agent without an agent user)
        if (c.get("agents") or {}).get("includeAgentUsers"):
            reasons.append("targets agent users; this agent has no agent user")

        results.append({
            "id": p.get("id"), "name": p.get("displayName"), "state": p.get("state"),
            "applies": bool(targets), "targets": targets, "reasons": reasons,
            "grant": _control_summary(p), "conditions": _conditions_summary(c),
        })
    order = {"enabled": 0, "enabledForReportingButNotEnforced": 1, "disabled": 2}
    return sorted(results, key=lambda r: (not r["applies"], order.get(r["state"], 3), r["name"] or ""))


def directory_info(user_oid: str | None) -> dict:
    if not settings.diag_admin_graph:
        return {"enabled": False, "note": "DIAG_ADMIN_GRAPH=false"}
    try:
        token = _az_graph_token()
    except Exception as e:
        return {"enabled": True, "error": str(e)}
    g = Graph(token)
    reader = decode_claims(token)
    agent, bp = settings.agent_id, settings.blueprint_id
    calls = {
        "agent": f"/beta/servicePrincipals(appId='{agent}')?$select=id,appId,displayName,accountEnabled,"
                 "servicePrincipalType,createdDateTime,createdByAppId,agentIdentityBlueprintId,tags",
        "sponsors": f"/beta/servicePrincipals/{agent}/sponsors?$select=id,displayName,userPrincipalName",
        "owners": f"/beta/servicePrincipals/{agent}/owners?$select=id,displayName,userPrincipalName",
        "blueprint": f"/beta/applications(appId='{bp}')?$select=id,appId,displayName,signInAudience,createdDateTime",
        "blueprintPrincipal": f"/beta/servicePrincipals(appId='{bp}')?$select=id,displayName",
        "inheritable": f"/beta/applications/{bp}/microsoft.graph.agentIdentityBlueprint/inheritablePermissions",
        "grants": f"/v1.0/oauth2PermissionGrants?$filter=clientId eq '{agent}'",
        "appRoles": f"/v1.0/servicePrincipals/{agent}/appRoleAssignments",
        "webClient": f"/v1.0/applications(appId='{settings.web_client_id}')?$select=id,appId,displayName,web",
        "ca": "/beta/identity/conditionalAccess/policies",
        "csa": f"/beta/servicePrincipals/{agent}?$select=customSecurityAttributes",
    }
    if user_oid:
        calls["memberOf"] = f"/v1.0/users/{user_oid}/transitiveMemberOf?$select=id,displayName,roleTemplateId&$top=999"
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = dict(zip(calls, ex.map(g.safe, calls.values())))

    bp_sp_id = (res["blueprintPrincipal"] or {}).get("id")
    grant_list = (res["grants"].get("value") or []) if "_error" not in res["grants"] else []
    if bp_sp_id:
        bp_grants = g.safe(f"/v1.0/oauth2PermissionGrants?$filter=clientId eq '{bp_sp_id}'")
        grant_list += [x | {"_via": "blueprint"} for x in bp_grants.get("value") or []]
    names = _sp_names(g, {x["resourceId"] for x in grant_list})
    grants = [{"resource": names.get(x["resourceId"], x["resourceId"]), "scope": x.get("scope", "").strip(),
               "consent": "tenant-wide (all users)" if x.get("consentType") == "AllPrincipals" else "single user",
               "holder": "blueprint (inherited by agent identities)" if x.get("_via") else "agent identity"}
              for x in grant_list]

    inherit = []
    inh_list = res["inheritable"].get("value") or []
    inh_names = _sp_names(g, {x["resourceAppId"] for x in inh_list}, by="appId")
    for x in inh_list:
        sc = x.get("inheritableScopes") or {}
        inherit.append({"resource": inh_names.get(x["resourceAppId"], x["resourceAppId"]),
                        "resourceAppId": x["resourceAppId"],
                        "scopes": sc.get("kind") if sc.get("kind") != "enumerated" else ", ".join(sc.get("scopes") or [])})

    members = res.get("memberOf", {}).get("value") or []
    member_ids = {m["id"] for m in members}
    role_ids = {m["roleTemplateId"] for m in members if m.get("roleTemplateId")}
    ca = res["ca"]
    wc = res["webClient"]
    return {
        "enabled": True,
        "source": f"Microsoft Graph via Azure CLI session of {reader.get('upn') or reader.get('unique_name') or 'unknown'}",
        "agentIdentity": res["agent"],
        "sponsors": [s.get("displayName") + (f" ({s['userPrincipalName']})" if s.get("userPrincipalName") else "")
                     for s in res["sponsors"].get("value") or []] if "_error" not in res["sponsors"] else res["sponsors"],
        "owners": [s.get("displayName") for s in res["owners"].get("value") or []],
        "customSecurityAttributes": res["csa"].get("customSecurityAttributes") if "_error" not in res["csa"]
        else {"_error": res["csa"]["_error"]},
        "blueprint": res["blueprint"] | {"servicePrincipalId": bp_sp_id},
        "webClient": {k: wc.get(k) for k in ("appId", "displayName")} | {
            "redirectUris": (wc.get("web") or {}).get("redirectUris")} if "_error" not in wc else wc,
        "inheritablePermissions": inherit if "_error" not in res["inheritable"] else res["inheritable"],
        "grants": grants if "_error" not in res["grants"] else res["grants"],
        "appRoleAssignments": [{"resource": a.get("resourceDisplayName"), "appRoleId": a.get("appRoleId")}
                               for a in res["appRoles"].get("value") or []],
        "conditionalAccess": evaluate_ca(ca.get("value") or [], user_oid or "", member_ids, role_ids)
        if "_error" not in ca else ca,
    }


# ── Purview policies ───────────────────────────────────────────────────────────────────────────────────────────────

def purview_info(user_oid: str, user_token: str | None) -> dict:
    if not settings.purview_enabled:
        return {"enabled": False}
    if not user_token:
        return {"enabled": True, "error": "no user token"}
    try:
        s = purview.compute_scopes(user_oid, auth.purview_token_for_user(user_oid, user_token), force=True)
    except Exception as e:
        return {"enabled": True, "error": str(e)}
    return {
        "enabled": True,
        "applicationLocation": settings.purview_app_location,
        "modes": {a: s.mode_for(a) or "none" for a in purview.ACTIVITIES},
        "failClosed": settings.purview_fail_closed,
        "scopes": [{
            "activities": x.get("activities"),
            "executionMode": x.get("executionMode"),
            "locations": [loc.get("value") for loc in x.get("locations") or []],
            "policyActions": [a.get("action") or a.get("@odata.type", "").split(".")[-1]
                              for a in x.get("policyActions") or []],
            "raw": x,
        } for x in s.scopes],
    }


# ── Entry point ────────────────────────────────────────────────────────────────────────────────────────────────────

def _registration_id() -> str | None:
    if os.getenv("AGENT365_REGISTRATION_ID"):
        return os.getenv("AGENT365_REGISTRATION_ID")
    try:
        return json.loads((ROOT / "a365.generated.config.json").read_text(encoding="utf-8")).get("agentRegistrationId")
    except Exception:
        return None


def collect(state: dict, user: dict | None) -> dict:
    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "tenantId": settings.tenant_id, "agentName": settings.agent_name, "agentIdentityId": settings.agent_id,
            "blueprintId": settings.blueprint_id, "webClientId": settings.web_client_id,
            "blueprintScope": settings.blueprint_scope, "agentRegistrationId": _registration_id(),
            "authEnabled": settings.auth_enabled, "observabilityExporter": settings.a365_exporter,
            "purviewEnabled": settings.purview_enabled, "mcpScope": settings.mcp_scope or None,
        },
        "user": None, "tokens": [], "purview": None, "directory": None,
    }
    if not settings.auth_enabled:
        out["note"] = "AUTH_ENABLED=false: no Entra sign-in, so no tokens or policies apply."
        return out
    if not user:
        return out
    idc = state.get("id_claims") or {}
    out["user"] = {k: idc.get(k, user.get(k)) for k in ("name", "preferred_username", "oid", "tid")} | {
        "upn": user.get("upn"), "idTokenClaims": idc or None}
    with ThreadPoolExecutor(max_workers=2) as ex:
        dir_future = ex.submit(directory_info, user.get("oid"))
        out["tokens"], user_token = token_chain(state, user)
        out["purview"] = purview_info(user["oid"], user_token)
        out["directory"] = dir_future.result()
    return out
