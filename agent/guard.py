"""Prompt/response guard: enforces Microsoft Purview policies (see agent/purview.py).

prompt_guard   -> uploadText   before the LLM runs (a DLP block stops the turn; nothing reaches the model).
response_guard -> downloadText before the reply is returned to the user.
Both are no-ops when auth or PURVIEW_ENABLED is off (the Purview APIs need a signed-in user).
"""
from dataclasses import dataclass

from agent import purview
from agent.config import settings


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    status: str = "disabled"


def _check(ctx: "purview.TurnContext | None", activity: str, text: str) -> GuardResult:
    if ctx is None or not (settings.auth_enabled and settings.purview_enabled):
        return GuardResult(allowed=True)
    d = purview.evaluate(ctx, activity, text)
    return GuardResult(allowed=d.allowed, reason=d.reason, status=d.status)


def prompt_guard(ctx: "purview.TurnContext | None", prompt: str) -> GuardResult:
    return _check(ctx, "uploadText", prompt)


def response_guard(ctx: "purview.TurnContext | None", reply: str) -> GuardResult:
    return _check(ctx, "downloadText", reply)
