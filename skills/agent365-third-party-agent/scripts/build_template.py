"""Rebuild assets/template from a working reference repo (sanitized).

Usage: python build_template.py <reference-repo> [--agent-name NAME] [--provider NAME]

Copies the source files of a proven Agent 365 3rd-party agent, strips tenant-specific
values and turns the agent/provider name into {{AGENT_NAME}} / {{PROVIDER_NAME}} tokens
that scripts/new_agent.py fills in. Secrets and generated files are never copied.
"""
import argparse
import json
import re
import shutil
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
DEST = SKILL / "assets" / "template"

INCLUDE_DIRS = ["agent", "web", "scripts", "tests", "docs"]
INCLUDE_FILES = [".env.example", ".gitignore", "requirements.txt", "README.md"]
SKIP = re.compile(r"(__pycache__|\.pyc$|\.log$|^\.env$|a365\.generated|appsettings|^~\$)")
TEXT = {".py", ".ps1", ".md", ".html", ".txt", ".json", ".example", ".gitignore", ".css", ".js"}
GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
ENV_IDS = {"WEB_CLIENT_ID": "web-client-id", "AGENT365_TENANT_ID": "tenant-id",
           "AGENT365_AGENT_ID": "agent-identity-id", "AGENT365_BLUEPRINT_ID": "blueprint-app-id"}
CONFIG_IDS = {
    "a365.config.json": {"tenantId": "tenant-id", "clientAppId": "a365-cli-client-app-id"},
    "a365.generated.config.json": {
        "agentBlueprintId": "blueprint-app-id", "agentBlueprintObjectId": "blueprint-object-id",
        "agentBlueprintServicePrincipalObjectId": "blueprint-sp-id", "agenticAppId": "agent-identity-id",
        "agentRegistrationId": "agent-registration-id"},
}


def tenant_map(repo: Path) -> dict[str, tuple[str, str]]:
    """Real tenant GUID -> (doc placeholder, fake GUID for offline test harnesses)."""
    m: dict[str, tuple[str, str]] = {}

    def add(val, name):
        if val and re.fullmatch(GUID, str(val)) and str(val) not in m:
            m[str(val)] = (f"<{name}>", f"00000000-0000-0000-0000-{len(m) + 1:012d}")

    for f, keys in CONFIG_IDS.items():
        p = repo / f
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            for k, name in keys.items():
                add(data.get(k), name)
    for env in repo.glob(".env*"):
        if env.name.endswith(".example") or not env.is_file():
            continue
        for line in env.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() in ENV_IDS:
                add(v.strip(), ENV_IDS[k.strip()])
    return m


def sanitize(text, rel: Path, ids, agent, provider, extra):
    fake = "dev" in rel.parts or "tests" in rel.parts
    for real, (ph, fg) in ids.items():
        text = text.replace(real, fg if fake else ph)
        # Truncated GUID forms used in documentation.
        text = re.sub(re.escape(real[:8]) + r"-(?:…|\.\.\.)", ph, text)
    for a, b in extra.items():
        text = text.replace(a, b)
    text = text.replace(provider, "{{PROVIDER_NAME}}")
    # Numbered instances (e.g. "...Demo2" from New-AgentInstance.ps1) -> {{AGENT_NAME}}-2, before the base name.
    stem = re.sub(r"\d+$", "", agent)
    if stem != agent:
        text = re.sub(re.escape(stem) + r"(?!" + re.escape(agent[len(stem):]) + r"\b)(\d+)\b", r"{{AGENT_NAME}}-\1", text)
    text = text.replace(agent, "{{AGENT_NAME}}")
    if stem != agent:
        text = text.replace(stem, "{{AGENT_NAME}}")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path)
    ap.add_argument("--agent-name", default="Laszlo-AgentRegistryDemo1")
    ap.add_argument("--provider", default="Laszlo Demo ISV")
    ap.add_argument("--domain", default="contosotpm.onmicrosoft.com")
    ap.add_argument("--admin", default="admin")
    ap.add_argument("--extra-id", action="append", default=[], help="GUID=placeholder-name")
    a = ap.parse_args()

    ids = tenant_map(a.repo)
    for kv in a.extra_id:
        g, _, name = kv.partition("=")
        ids[g] = (f"<{name}>", f"00000000-0000-0000-0000-{900 + len(ids):012d}")
    extra = {f"{a.admin}@{a.domain}": "admin@contoso.onmicrosoft.com", a.domain: "contoso.onmicrosoft.com"}

    if DEST.exists():
        shutil.rmtree(DEST)
    files = [a.repo / f for f in INCLUDE_FILES if (a.repo / f).exists()]
    for d in INCLUDE_DIRS:
        files += [p for p in (a.repo / d).rglob("*") if p.is_file()]
    n = 0
    for src in files:
        rel = src.relative_to(a.repo)
        if rel.parts[:3] == ("docs", "deck", "build") or rel.parts[:2] == ("docs", "planning"):
            continue
        if src.suffix not in TEXT and src.name not in TEXT:
            continue
        if any(SKIP.search(p) for p in rel.parts):
            continue
        out = DEST / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in TEXT or src.name in TEXT:
            text = sanitize(src.read_text(encoding="utf-8"), rel, ids, a.agent_name, a.provider, extra)
            out.write_bytes(text.encode("utf-8"))
        else:
            shutil.copy2(src, out)
        n += 1
    needles = list(ids) + [g[:8] + "-" for g in ids] + [a.domain, a.agent_name, a.provider]
    leftovers = sorted({str(p.relative_to(DEST)) for p in DEST.rglob("*")
                        if p.is_file() and any(s in p.read_text(encoding="utf-8", errors="ignore") for s in needles)})
    print(f"Template rebuilt: {n} files -> {DEST}")
    print(f"Sanitized tenant IDs: {len(ids)}; leftovers: {leftovers or 'none'}")
    if leftovers:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
