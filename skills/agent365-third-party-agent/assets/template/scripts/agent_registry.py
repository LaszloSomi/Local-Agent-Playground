"""Get-or-create the Agent 365 registry record (Graph /beta/copilot/agentRegistrations) for an agent identity.

The a365 CLI only registers the first identity of a blueprint. Extra instances are registered here, signed in as the
presenter through the a365 CLI client app (public client, http://localhost loopback, so no WAM window-handle issue).
Needs AgentRegistration.ReadWrite.All (delegated). Prints the registration as JSON.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import httpx
import msal

GRAPH = "https://graph.microsoft.com/beta/copilot/agentRegistrations"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant-id", required=True)
    ap.add_argument("--client-id", required=True, help="a365 CLI client app (a365.config.json clientAppId)")
    ap.add_argument("--agent-id", required=True)
    ap.add_argument("--blueprint-id", required=True)
    ap.add_argument("--owner-oid", required=True)
    ap.add_argument("--display-name", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--registration-id", default="", help="existing record to verify instead of creating")
    ap.add_argument("--login-hint", default=None)
    ap.add_argument("--cache", default=os.path.join(os.getenv("LOCALAPPDATA") or os.path.expanduser("~"),
                                                    "agent365-demo", "registry_msal_cache.bin"),
                    help="MSAL token cache file (user profile, never the repo) so reruns don't prompt")
    a = ap.parse_args()

    cache = msal.SerializableTokenCache()
    if os.path.exists(a.cache):
        cache.deserialize(open(a.cache, encoding="utf-8").read())
    app = msal.PublicClientApplication(a.client_id, authority=f"https://login.microsoftonline.com/{a.tenant_id}",
                                       token_cache=cache)
    scopes = ["https://graph.microsoft.com/AgentRegistration.ReadWrite.All"]
    accts = app.get_accounts(username=a.login_hint) or app.get_accounts()
    tok = app.acquire_token_silent(scopes, account=accts[0]) if accts else None
    if not tok or "access_token" not in tok:
        tok = app.acquire_token_interactive(scopes, login_hint=a.login_hint, timeout=300)
    if cache.has_state_changed:
        os.makedirs(os.path.dirname(a.cache), exist_ok=True)
        with open(a.cache, "w", encoding="utf-8") as f:
            f.write(cache.serialize())
    if "access_token" not in tok:
        print(json.dumps({"error": tok.get("error"), "description": tok.get("error_description")}))
        return 1
    h = {"Authorization": f"Bearer {tok['access_token']}"}

    for rid in filter(None, [a.registration_id, a.agent_id]):  # the service reuses the agent identity ID as record ID
        r = httpx.get(f"{GRAPH}/{rid}", headers=h, timeout=60)
        if r.status_code == 200 and r.json().get("agentIdentityId") == a.agent_id:
            print(json.dumps({"status": "exists", **r.json()}))
            return 0

    now = datetime.now(timezone.utc).isoformat()
    body = {"displayName": a.display_name, "description": a.description, "createdBy": a.owner_oid,
            "ownerIds": [a.owner_oid], "sourceCreatedDateTime": now, "sourceLastModifiedDateTime": now,
            "agentIdentityId": a.agent_id, "agentIdentityBlueprintId": a.blueprint_id, "sourceAgentId": a.agent_id}
    r = httpx.post(GRAPH, headers=h, json=body, timeout=60)
    if r.status_code not in (200, 201):
        print(json.dumps({"error": r.status_code, "body": r.text[:500]}))
        return 1
    print(json.dumps({"status": "created", **r.json()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
