"""Make tests hermetic before agent.config loads: fill blank Agent 365 IDs (e.g. a freshly scaffolded,
not-yet-registered .env) with fake GUIDs. Real values from .env or the environment are never overridden."""
import os

from dotenv import dotenv_values

_FAKE = {
    "AGENT365_TENANT_ID": "00000000-0000-0000-0000-0000000000a1",
    "AGENT365_BLUEPRINT_ID": "00000000-0000-0000-0000-0000000000a2",
    "AGENT365_AGENT_ID": "00000000-0000-0000-0000-0000000000a3",
}
_file = dotenv_values(".env")
for _k, _v in _FAKE.items():
    if not (os.environ.get(_k) or _file.get(_k)):
        os.environ[_k] = _v
