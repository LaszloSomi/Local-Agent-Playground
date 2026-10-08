"""Microsoft Purview APIs (Microsoft Graph) for the agent: the "Purview SDK" integration.

Per the Purview developer tutorial (https://learn.microsoft.com/purview/developer/use-the-api):
  1. protectionScopes/compute  -> which activities (uploadText = prompt, downloadText = response) Purview policies
     cover for this user and how: evaluateInline (block the turn until processContent returns) or evaluateOffline.
     Cached per user with its ETag; recomputed after 60 min or when processContent reports "modified".
  2. processContent            -> sends the prompt/response TEXT for DLP evaluation. This is what makes the prompt and
     response appear in DSPM for AI / Activity explorer. policyActions restrictAccess+block => the agent must block.
  3. activities/contentActivities -> metadata-only audit record when no protection scope applies.

All calls run as the agent identity on behalf of the signed-in user (auth.purview_token_for_user), using the
inheritable Graph permissions on the blueprint (Content.Process.User, ProtectionScopes.Compute.User,
ContentActivity.Write). Requests include the aiAgentInfo block so Purview attributes content to this agent.
"""
from __future__ import annotations

import logging
import platform
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from agent.config import settings

log = logging.getLogger("agent.purview")

GRAPH = "https://graph.microsoft.com/v1.0/me/dataSecurityAndGovernance"
SCOPE_TTL_SECONDS = 60 * 60
ACTIVITIES = ("uploadText", "downloadText")
BLOCK_MESSAGE = "Blocked by your organization's data protection policy (Microsoft Purview)."

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="purview")
_lock = threading.Lock()


@dataclass
class UserScopes:
    etag: str | None
    scopes: list[dict]
    fetched_at: float = field(default_factory=time.time)

    def mode_for(self, activity: str) -> str | None:
        """Most restrictive execution mode across scopes covering `activity` (inline beats offline)."""
        modes = {s.get("executionMode") for s in self.scopes
                 if activity in [a.strip() for a in (s.get("activities") or "").split(",")]}
        if "evaluateInline" in modes:
            return "evaluateInline"
        if "evaluateOffline" in modes:
            return "evaluateOffline"
        return None


@dataclass
class TurnContext:
    user_oid: str
    token: str
    conversation_id: str
    sequence: int
    client_ip: str | None = None


@dataclass
class Decision:
    allowed: bool = True
    status: str = "disabled"
    reason: str = ""
    actions: list[dict] = field(default_factory=list)


_scope_cache: dict[str, UserScopes] = {}


def _headers(token: str, etag: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
         "client-request-id": str(uuid.uuid4())}
    if etag:
        h["If-None-Match"] = etag
    return h


def _location() -> dict:
    return {"@odata.type": "microsoft.graph.policyLocationApplication", "value": settings.purview_app_location}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _app_metadata() -> dict:
    return {
        "protectedAppMetadata": {"name": settings.agent_name, "version": "1.0.0", "applicationLocation": _location()},
        "integratedAppMetadata": {"name": settings.agent_name, "version": "1.0.0"},
    }


def _device(ip: str | None) -> dict:
    d = {"operatingSystemSpecifications": {"operatingSystemPlatform": platform.system() or "Windows",
                                           "operatingSystemVersion": platform.version() or "unknown"}}
    if ip:
        d["ipAddress"] = ip
    return d


def _agent_info() -> list[dict]:
    return [{
        "@odata.type": "microsoft.graph.aiAgentInfo",
        "identifier": settings.agent_id,
        "blueprintId": settings.blueprint_id,
        "name": settings.agent_name,
        "version": "1.0.0",
    }]


def _entry(ctx: TurnContext, activity: str, text: str | None) -> dict:
    entry = {
        "@odata.type": "microsoft.graph.processConversationMetadata",
        "identifier": str(uuid.uuid4()),
        "name": f"{settings.agent_name} {'prompt' if activity == 'uploadText' else 'response'}",
        "correlationId": ctx.conversation_id,
        "sequenceNumber": ctx.sequence,
        "isTruncated": False,
        "createdDateTime": _now(),
        "modifiedDateTime": _now(),
        "agents": _agent_info(),
    }
    if text is not None:
        entry["content"] = {"@odata.type": "microsoft.graph.textContent", "data": text}
    return entry


def _payload(ctx: TurnContext, activity: str, text: str | None) -> dict:
    return {"contentToProcess": {
        "contentEntries": [_entry(ctx, activity, text)],
        "activityMetadata": {"activity": activity},
        "deviceMetadata": _device(ctx.client_ip),
        **_app_metadata(),
    }}


def compute_scopes(user_oid: str, token: str, force: bool = False) -> UserScopes:
    with _lock:
        cached = _scope_cache.get(user_oid)
    if cached and not force and time.time() - cached.fetched_at < SCOPE_TTL_SECONDS:
        return cached
    r = httpx.post(f"{GRAPH}/protectionScopes/compute", headers=_headers(token), timeout=20,
                   json={"activities": ",".join(ACTIVITIES), "locations": [_location()]})
    if r.status_code >= 400:
        raise RuntimeError(f"protectionScopes/compute HTTP {r.status_code}: {r.text[:300]}")
    scopes = UserScopes(etag=r.headers.get("ETag"), scopes=r.json().get("value", []))
    with _lock:
        _scope_cache[user_oid] = scopes
    log.info("Purview protection scopes for %s: %s", user_oid,
             {a: scopes.mode_for(a) or "none" for a in ACTIVITIES})
    return scopes


def process_content(ctx: TurnContext, activity: str, text: str, etag: str | None) -> Decision:
    r = httpx.post(f"{GRAPH}/processContent", headers=_headers(ctx.token, etag), timeout=30,
                   json=_payload(ctx, activity, text))
    if r.status_code >= 400:
        raise RuntimeError(f"processContent HTTP {r.status_code}: {r.text[:300]}")
    body = r.json() if r.status_code == 200 and r.content else {}
    if body.get("protectionScopeState") == "modified":
        with _lock:
            _scope_cache.pop(ctx.user_oid, None)
    actions = body.get("policyActions") or []
    errors = body.get("processingErrors") or []
    if errors:
        log.warning("processContent processingErrors (%s): %s", activity, errors)
    blocked = any(a.get("action") == "restrictAccess" and a.get("restrictionAction") == "block" for a in actions)
    log.info("processContent %s -> HTTP %s, actions=%s", activity, r.status_code,
             [a.get("restrictionAction") or a.get("action") for a in actions])
    return Decision(allowed=not blocked, status="blocked" if blocked else "allowed",
                    reason=BLOCK_MESSAGE if blocked else "", actions=actions)


def log_content_activity(ctx: TurnContext, activity: str) -> None:
    r = httpx.post(f"{GRAPH}/activities/contentActivities", headers=_headers(ctx.token), timeout=20,
                   json=_payload(ctx, activity, None))
    if r.status_code >= 400:
        raise RuntimeError(f"contentActivities HTTP {r.status_code}: {r.text[:300]}")
    log.info("contentActivities %s -> HTTP %s", activity, r.status_code)


def _background(fn, *args) -> None:
    def run():
        try:
            fn(*args)
        except Exception as e:
            log.warning("Purview background call failed: %s", e)
    _pool.submit(run)


def evaluate(ctx: TurnContext, activity: str, text: str) -> Decision:
    """Apply the user's protection scope to one prompt (uploadText) or response (downloadText)."""
    try:
        scopes = compute_scopes(ctx.user_oid, ctx.token)
        mode = scopes.mode_for(activity)
        if mode == "evaluateInline":
            d = process_content(ctx, activity, text, scopes.etag)
            d.status = f"inline-{d.status}"
            return d
        if mode == "evaluateOffline":
            _background(process_content, ctx, activity, text, scopes.etag)
            return Decision(status="offline-sent")
        if settings.purview_log_when_unscoped:
            _background(log_content_activity, ctx, activity)
            return Decision(status="no-scope-activity-logged")
        return Decision(status="no-scope")
    except Exception as e:
        log.warning("Purview %s check failed: %s", activity, e)
        if settings.purview_fail_closed:
            return Decision(allowed=False, status="error-blocked", reason="Purview policy check unavailable.")
        return Decision(status=f"error: {e}")
