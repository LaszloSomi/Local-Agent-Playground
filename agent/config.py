import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Optional per-instance overlay (e.g. .env.agent2) loaded first so it wins; .env then fills in the shared values.
# Lets several agent identities from one blueprint run side by side (different ports, same web client/LLM).
if os.getenv("AGENT_ENV_FILE"):
    load_dotenv(os.getenv("AGENT_ENV_FILE"))
load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    app_host: str = os.getenv("APP_HOST", "localhost")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    session_secret: str = os.getenv("SESSION_SECRET", "dev-only-secret")
    auth_enabled: bool = _bool("AUTH_ENABLED", False)

    llm_provider: str = os.getenv("LLM_PROVIDER", "ollama").lower()
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    github_model: str = os.getenv("GITHUB_MODELS_MODEL", "openai/gpt-4.1-mini")
    github_endpoint: str = os.getenv("GITHUB_MODELS_ENDPOINT", "https://models.github.ai/inference")
    github_token: str = os.getenv("GITHUB_TOKEN", "")

    tenant_id: str = os.getenv("AGENT365_TENANT_ID", "")
    agent_name: str = os.getenv("AGENT365_AGENT_NAME", "Laszlo-AgentRegistryDemo1")
    agent_description: str = os.getenv("AGENT365_AGENT_DESCRIPTION", "Weather and geocoding agent")
    blueprint_id: str = os.getenv("AGENT365_BLUEPRINT_ID", "")
    blueprint_secret: str = os.getenv("AGENT365_BLUEPRINT_CLIENT_SECRET", "")
    agent_id: str = os.getenv("AGENT365_AGENT_ID", "")
    blueprint_scope: str = os.getenv("AGENT365_BLUEPRINT_SCOPE", "")

    web_client_id: str = os.getenv("WEB_CLIENT_ID", "")
    web_client_secret: str = os.getenv("WEB_CLIENT_SECRET", "")
    web_redirect_uri: str = os.getenv("WEB_REDIRECT_URI", "http://localhost:8000/auth/callback")

    a365_exporter: bool = _bool("ENABLE_A365_OBSERVABILITY_EXPORTER", False)
    otlp_local_endpoint: str = os.getenv("OTLP_LOCAL_ENDPOINT", "")
    console_exporter: bool = _bool("OTEL_CONSOLE_EXPORTER", False)
    a365_verbose: bool = _bool("A365_VERBOSE", False)

    # Microsoft Purview APIs (Graph processContent / protectionScopes / contentActivities). Needs AUTH_ENABLED=true.
    purview_enabled: bool = _bool("PURVIEW_ENABLED", True)
    # policyLocationApplication value sent to Purview; defaults to the agent identity app ID (the Graph token's appid).
    purview_app_location: str = os.getenv("PURVIEW_APP_LOCATION_ID", "") or os.getenv("AGENT365_AGENT_ID", "")
    # Log content activity (metadata only) when no protection scope applies to the user.
    purview_log_when_unscoped: bool = _bool("PURVIEW_LOG_WHEN_UNSCOPED", True)
    # true = block the turn if an inline Purview check can't be completed (production); false = fail open (demo).
    purview_fail_closed: bool = _bool("PURVIEW_FAIL_CLOSED", False)

    # Optional MCP server scope (e.g. "<mcp-app-id>/.default"). Empty = the agent calls no MCP servers.
    mcp_scope: str = os.getenv("MCP_SCOPE", "")
    # Diagnostics tab: read directory/CA data from Graph with this machine's Azure CLI session (az login).
    diag_admin_graph: bool = _bool("DIAG_ADMIN_GRAPH", True)

    @property
    def model_label(self) -> str:
        return self.ollama_model if self.llm_provider == "ollama" else self.github_model

    @property
    def provider_label(self) -> str:
        return "ollama" if self.llm_provider == "ollama" else "github-models"


settings = Settings()
