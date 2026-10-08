"""LangGraph tool-calling agent (the '3rd-party' agent being brought under Agent 365)."""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from agent.config import settings
from agent.tools import ALL_TOOLS

SYSTEM_PROMPT = (
    "You are WeatherGeo, a concise assistant. Use the get_weather tool for current weather "
    "and the geocode tool for coordinates/location details. Always call a tool rather than "
    "guessing. Answer in 1-3 short sentences and include units."
)


def _build_llm():
    if settings.llm_provider == "github":
        from langchain_openai import ChatOpenAI

        if not settings.github_token:
            raise RuntimeError("LLM_PROVIDER=github requires GITHUB_TOKEN")
        return ChatOpenAI(
            model=settings.github_model,
            base_url=settings.github_endpoint,
            api_key=settings.github_token,
            temperature=0,
        )
    from langchain_ollama import ChatOllama

    return ChatOllama(model=settings.ollama_model, base_url=settings.ollama_base_url, temperature=0)


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = create_react_agent(_build_llm(), ALL_TOOLS, name=settings.agent_name)
    return _graph


def run_agent(prompt: str, history: list[dict] | None = None, callbacks: list | None = None) -> dict:
    """Run one turn. Returns final text plus the tool calls made (for UI display)."""
    messages = [SystemMessage(SYSTEM_PROMPT)]
    for h in history or []:
        messages.append(HumanMessage(h["content"]) if h["role"] == "user" else AIMessage(h["content"]))
    messages.append(HumanMessage(prompt))

    result = get_graph().invoke({"messages": messages}, config={"callbacks": callbacks or []})
    out = result["messages"]
    tool_calls = [
        {"name": tc["name"], "args": tc["args"]}
        for m in out if isinstance(m, AIMessage) for tc in (m.tool_calls or [])
    ]
    final = next((m.content for m in reversed(out) if isinstance(m, AIMessage) and m.content), "")
    return {"text": final, "tool_calls": tool_calls}
