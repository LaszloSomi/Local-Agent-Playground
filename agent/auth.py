"""Entra ID sign-in (web client) + Agent ID on-behalf-of token chain for Agent 365 observability.

Flow (see docs/02-register.md):
  1. User signs in to the WEB CLIENT app (auth-code flow) and gets Tc with aud = blueprint.
  2. Blueprint authenticates (client secret, demo only) -> T1 for api://AzureADTokenExchange
     with fmi_path = agent identity app ID.
  3. Agent identity exchanges T1 + Tc (OBO) -> token for Agent 365 Observability
     (scope 9b975845-.../Agent365.Observability.OtelWrite). The exporter uses this token.
  4. Same exchange for Microsoft Graph Purview scopes -> agent/purview.py (processContent etc.).
"""
import logging
import time

import httpx
import msal

from agent.config import settings

log = logging.getLogger("agent.auth")

OBSERVABILITY_OBO_SCOPE = "9b975845-388f-4429-889e-eab1ef63949c/Agent365.Observability.OtelWrite"
FMI_SCOPE = "api://AzureADTokenExchange/.default"
PURVIEW_OBO_SCOPE = " ".join(f"https://graph.microsoft.com/{s}" for s in (
    "ProtectionScopes.Compute.User", "Content.Process.User", "ContentActivity.Write"))


def _authority() -> str:
    return f"https://login.microsoftonline.com/{settings.tenant_id}"


def _token_url() -> str:
    return f"{_authority()}/oauth2/v2.0/token"


def web_client(cache: msal.SerializableTokenCache | None = None) -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        settings.web_client_id,
        authority=_authority(),
        client_credential=settings.web_client_secret,
        token_cache=cache,
    )


def login_scopes() -> list[str]:
    return [settings.blueprint_scope] if settings.blueprint_scope else []


def start_auth_flow() -> dict:
    return web_client().initiate_auth_code_flow(login_scopes(), redirect_uri=settings.web_redirect_uri)


def complete_auth_flow(flow: dict, query: dict) -> dict:
    """Returns the MSAL result plus 'token_cache' (serialized) so the session can refresh Tc later."""
    cache = msal.SerializableTokenCache()
    result = web_client(cache).acquire_token_by_auth_code_flow(flow, query)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description") or str(result))
    result["token_cache"] = cache.serialize()
    return result


class ReauthRequired(RuntimeError):
    pass


def fresh_user_token(state: dict) -> str:
    """Return a user token (Tc) valid for >= 5 minutes, silently refreshing via the session's
    MSAL cache. A user access token lives ~60-90 min; without this, the OBO exchange fails with
    AADSTS500133 and the exporter keeps sending the last (expired) observability token -> HTTP 401."""
    cache = msal.SerializableTokenCache()
    if state.get("token_cache"):
        cache.deserialize(state["token_cache"])
    app = web_client(cache)
    accounts = [a for a in app.get_accounts() if a.get("home_account_id") == state.get("home_account_id")]
    result = app.acquire_token_silent(login_scopes(), account=accounts[0]) if accounts else None
    if not result or "access_token" not in result:
        detail = (result or {}).get("error_description", "no cached account/refresh token")
        raise ReauthRequired(f"User token can't be refreshed silently; sign in again ({detail})")
    if cache.has_state_changed:
        state["token_cache"] = cache.serialize()
    state["access_token"] = result["access_token"]
    return result["access_token"]


def jwt_expiry(token: str) -> float:
    """Unverified 'exp' claim (seconds since epoch) — used only to avoid sending expired tokens."""
    import base64
    import json

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return 0.0


_obo_cache: dict[str, tuple[str, float]] = {}


def _blueprint_t1() -> str:
    # MSAL Python doesn't yet support fmi_path natively; post to the token endpoint directly.
    r = httpx.post(_token_url(), data={
        "grant_type": "client_credentials",
        "client_id": settings.blueprint_id,
        "client_secret": settings.blueprint_secret,
        "scope": FMI_SCOPE,
        "fmi_path": settings.agent_id,
    }, timeout=20)
    body = r.json()
    if "access_token" not in body:
        raise RuntimeError(f"Blueprint T1 failed: {body.get('error_description', body)}")
    return body["access_token"]


def observability_token_for_user(user_oid: str, user_token: str) -> str:
    """Agent identity OBO exchange -> Agent 365 observability token (cached ~50 min per user)."""
    return agent_obo_token(user_oid, user_token, OBSERVABILITY_OBO_SCOPE)


def purview_token_for_user(user_oid: str, user_token: str) -> str:
    """Agent identity OBO exchange -> Microsoft Graph token for the Purview APIs. The scopes are
    inheritable permissions on the blueprint, so the agent identity gets them without its own grant."""
    return agent_obo_token(user_oid, user_token, PURVIEW_OBO_SCOPE)


def agent_obo_token(user_oid: str, user_token: str, scope: str) -> str:
    """Blueprint T1 + user Tc -> agent identity token for `scope` (cached ~50 min per user+scope)."""
    key = f"{user_oid}|{scope}"
    cached = _obo_cache.get(key)
    if cached and cached[1] > time.time():
        return cached[0]
    if jwt_expiry(user_token) < time.time() + 60:
        raise ReauthRequired("User token expired; sign in again")
    t1 = _blueprint_t1()
    r = httpx.post(_token_url(), data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "client_id": settings.agent_id,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": t1,
        "assertion": user_token,
        "requested_token_use": "on_behalf_of",
        "scope": scope,
    }, timeout=20)
    body = r.json()
    if "access_token" not in body:
        raise RuntimeError(f"Agent identity OBO failed ({scope}): {body.get('error_description', body)}")
    ttl = int(body.get("expires_in", 3600)) - 600
    _obo_cache[key] = (body["access_token"], time.time() + ttl)
    return body["access_token"]
