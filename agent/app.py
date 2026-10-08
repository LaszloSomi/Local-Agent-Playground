"""FastAPI web chat for the Laszlo-AgentRegistryDemo1 3rd-party agent."""
import logging
import os
import secrets
import uuid

from agent.config import settings
from agent.telemetry import cache_observability_token, init_telemetry

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
init_telemetry()  # before LangChain/LangGraph imports

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse  # noqa: E402
from microsoft.opentelemetry.a365.core import (  # noqa: E402
    AgentDetails,
    BaggageBuilder,
    CallerDetails,
    Channel,
    InvokeAgentScope,
    InvokeAgentScopeDetails,
    Request as A365Request,
    ServiceEndpoint,
    UserDetails,
)
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from agent import auth, diagnostics, purview  # noqa: E402
from agent.a365_callbacks import A365ScopeCallback  # noqa: E402
from agent.graph import run_agent  # noqa: E402
from agent.guard import prompt_guard, response_guard  # noqa: E402

log = logging.getLogger("agent.app")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title=settings.agent_name)
# Cookies are shared across ports on localhost, so name the cookie per port to keep instances' sign-ins apart.
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, https_only=False, same_site="lax",
                   session_cookie=f"session_{settings.app_port}")

# Server-side session store (tokens are too large for cookies). In-memory is fine for a local demo.
_SESSIONS: dict[str, dict] = {}


def _sid(request: Request) -> str:
    sid = request.session.get("sid")
    if not sid:
        sid = secrets.token_urlsafe(16)
        request.session["sid"] = sid
    _SESSIONS.setdefault(sid, {"history": [], "conversation_id": str(uuid.uuid4())})
    return sid


def _state(request: Request) -> dict:
    return _SESSIONS[_sid(request)]


def _current_user(state: dict) -> dict | None:
    if not settings.auth_enabled:
        return {"oid": "local-user", "upn": "local.user@localhost", "name": "Local User (auth disabled)"}
    return state.get("user")


def _client_ip(request: Request) -> str | None:
    import ipaddress

    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def _agent_details() -> AgentDetails:
    return AgentDetails(
        agent_id=settings.agent_id or "local-dev-agent",
        agent_name=settings.agent_name,
        agent_description=settings.agent_description,
        agent_blueprint_id=settings.blueprint_id or None,
        tenant_id=settings.tenant_id or "local-dev-tenant",
        provider_name="Laszlo Demo ISV (LangGraph)",
        agent_version="1.0.0",
    )


@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "web", "index.html"))


@app.get("/api/me")
def me(request: Request):
    state = _state(request)
    user = _current_user(state)
    return {
        "authEnabled": settings.auth_enabled,
        "user": user,
        "agent": settings.agent_name,
        "model": f"{settings.provider_label}:{settings.model_label}",
        "a365Export": settings.a365_exporter and settings.auth_enabled,
        "purview": settings.purview_enabled and settings.auth_enabled,
    }


@app.get("/login")
def login(request: Request):
    if not settings.auth_enabled:
        return RedirectResponse("/")
    state = _state(request)
    flow = auth.start_auth_flow()
    state["flow"] = flow
    return RedirectResponse(flow["auth_uri"])


@app.get("/auth/callback")
def callback(request: Request):
    state = _state(request)
    flow = state.pop("flow", None)
    if not flow:
        return RedirectResponse("/")
    try:
        result = auth.complete_auth_flow(flow, dict(request.query_params))
    except Exception as e:  # CA blocks surface here, e.g. AADSTS53000 / AADSTS53003
        log.warning("Sign-in failed: %s", e)
        return JSONResponse({"error": "sign_in_failed", "detail": str(e)}, status_code=401)
    claims = result.get("id_token_claims", {})
    state["user"] = {
        "oid": claims.get("oid"),
        "upn": claims.get("preferred_username"),
        "name": claims.get("name"),
    }
    state["access_token"] = result["access_token"]
    state["id_claims"] = {k: v for k, v in claims.items() if k not in ("nonce", "aio", "rh", "uti")}
    state["token_cache"] = result.get("token_cache")
    state["home_account_id"] = f"{claims.get('oid')}.{claims.get('tid')}"  # MSAL format uid.utid
    state["history"] = []
    state["conversation_id"] = str(uuid.uuid4())
    return RedirectResponse("/")


@app.get("/logout")
def logout(request: Request):
    sid = request.session.get("sid")
    _SESSIONS.pop(sid, None)
    request.session.clear()
    if settings.auth_enabled and settings.tenant_id:
        return RedirectResponse(
            f"https://login.microsoftonline.com/{settings.tenant_id}/oauth2/v2.0/logout"
            f"?post_logout_redirect_uri=http://{settings.app_host}:{settings.app_port}/"
        )
    return RedirectResponse("/")


@app.post("/api/reset")
def reset(request: Request):
    state = _state(request)
    state["history"] = []
    state["conversation_id"] = str(uuid.uuid4())
    return {"ok": True}


@app.post("/api/chat")
async def chat(request: Request):
    state = _state(request)
    user = _current_user(state)
    if not user:
        return JSONResponse({"error": "not_signed_in"}, status_code=401)
    body = await request.json()
    prompt = (body.get("message") or "").strip()
    if not prompt:
        return JSONResponse({"error": "empty_message"}, status_code=400)

    obs_status = "disabled"
    user_token = None
    if settings.auth_enabled and (settings.a365_exporter or settings.purview_enabled):
        try:
            user_token = auth.fresh_user_token(state)
        except auth.ReauthRequired as e:
            # Don't run an untracked/unguarded turn: in a governance demo the interaction must reach Agent 365/Purview.
            log.warning("User token unavailable: %s", e)
            state.pop("user", None)
            return JSONResponse({"error": "session_expired", "detail": "Your sign-in expired. Sign in again."},
                                status_code=401)
    if settings.auth_enabled and settings.a365_exporter:
        try:
            tok = auth.observability_token_for_user(user["oid"], user_token)
            cache_observability_token(settings.agent_id, settings.tenant_id, tok)
            obs_status = "token-ok"
        except Exception as e:
            log.warning("Observability token exchange failed: %s", e)
            obs_status = f"token-error: {e}"

    pv_ctx, pv_status = None, "disabled"
    if settings.auth_enabled and settings.purview_enabled:
        try:
            pv_ctx = purview.TurnContext(
                user_oid=user["oid"],
                token=auth.purview_token_for_user(user["oid"], user_token),
                conversation_id=state["conversation_id"],
                sequence=len(state["history"]) // 2,
                client_ip=_client_ip(request),
            )
        except Exception as e:
            log.warning("Purview token exchange failed: %s", e)
            pv_status = f"token-error: {e}"
            if settings.purview_fail_closed:
                return {"reply": "Blocked: Purview policy check unavailable.", "blocked": True, "toolCalls": [],
                        "observability": obs_status, "purview": pv_status}

    agent_details = _agent_details()
    a365_request = A365Request(
        content=prompt,
        session_id=_sid(request),
        conversation_id=state["conversation_id"],
        channel=Channel(name="web"),
    )
    caller = CallerDetails(user_details=UserDetails(
        user_id=user.get("oid"),
        user_email=user.get("upn"),
        user_name=user.get("name"),
        user_client_ip=_client_ip(request),
    ))

    with (
        BaggageBuilder()
        .tenant_id(agent_details.tenant_id)
        .agent_id(agent_details.agent_id)
        .conversation_id(a365_request.conversation_id)
        .session_id(a365_request.session_id)
        .channel_name(a365_request.channel.name)
        .build()
    ):
        with InvokeAgentScope.start(
            a365_request,
            InvokeAgentScopeDetails(endpoint=ServiceEndpoint(hostname=settings.app_host, port=settings.app_port)),
            agent_details,
            caller,
        ) as scope:
            scope.record_input_messages([prompt])

            guard = prompt_guard(pv_ctx, prompt)
            pv_status = f"prompt:{guard.status}" if pv_ctx else pv_status
            if not guard.allowed:
                msg = guard.reason or "Blocked by policy."
                scope.record_output_messages([msg])
                return {"reply": msg, "blocked": True, "toolCalls": [], "observability": obs_status,
                        "purview": pv_status}

            cb = A365ScopeCallback(a365_request, agent_details, caller.user_details, scope.get_context())
            try:
                result = run_agent(prompt, state["history"], callbacks=[cb])
            except Exception as e:
                log.exception("Agent run failed")
                scope.record_error(e)
                return JSONResponse({"error": "agent_failed", "detail": str(e)}, status_code=500)

            out_guard = response_guard(pv_ctx, result["text"])
            if pv_ctx:
                pv_status += f" · response:{out_guard.status}"
            if not out_guard.allowed:
                result = {**result, "text": out_guard.reason or "Response blocked by policy.", "blocked": True}
            scope.record_output_messages([result["text"]])

    state["history"] += [{"role": "user", "content": prompt}, {"role": "assistant", "content": result["text"]}]
    state["history"] = state["history"][-10:]
    return {
        "reply": result["text"],
        "blocked": bool(result.get("blocked")),
        "toolCalls": result["tool_calls"],
        "conversationId": state["conversation_id"],
        "observability": obs_status,
        "purview": pv_status,
    }


@app.get("/api/purview/scopes")
def purview_scopes(request: Request):
    """Demo/diagnostic: which Purview protection scopes apply to the signed-in user for this agent."""
    state = _state(request)
    user = _current_user(state)
    if not (settings.auth_enabled and settings.purview_enabled):
        return {"enabled": False}
    if not user:
        return JSONResponse({"error": "not_signed_in"}, status_code=401)
    try:
        token = auth.purview_token_for_user(user["oid"], auth.fresh_user_token(state))
        s = purview.compute_scopes(user["oid"], token, force=True)
    except auth.ReauthRequired:
        return JSONResponse({"error": "session_expired"}, status_code=401)
    except Exception as e:
        return JSONResponse({"error": "purview_failed", "detail": str(e)}, status_code=502)
    return {
        "enabled": True,
        "applicationLocation": settings.purview_app_location,
        "modes": {a: s.mode_for(a) or "none" for a in purview.ACTIVITIES},
        "etag": s.etag,
        "scopes": s.scopes,
    }


@app.get("/api/diagnostics")
def diagnostics_view(request: Request):
    """Diagnostics tab: Entra ID sign-in, token chain (claims only), agent identity and the policies applied to it."""
    state = _state(request)
    user = _current_user(state)
    if settings.auth_enabled and not user:
        return JSONResponse({"error": "not_signed_in"}, status_code=401)
    try:
        return diagnostics.collect(state, user)
    except auth.ReauthRequired:
        return JSONResponse({"error": "session_expired"}, status_code=401)


def main():
    import uvicorn

    uvicorn.run(app, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
