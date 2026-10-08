"""Scaffold a new Agent 365 3rd-party agent project from the proven template.

Usage:
  python new_agent.py --dest D:\\Repos\\MyAgent --name Contoso-StockAgent \
      [--provider "Contoso ISV"] [--description "Stock quote agent"] [--tenant-id <guid>] [--force]

Creates a ready-to-run FastAPI + LangGraph agent with Entra OBO sign-in, Agent 365
observability (microsoft-opentelemetry), Purview SDK guards, a Diagnostics tab, setup/run/
registration scripts, tests and a full docs set. Afterwards: replace agent/tools.py and the
SYSTEM_PROMPT in agent/graph.py with the new agent's domain, then follow the skill workflow.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "template"
TEXT = {".py", ".ps1", ".md", ".html", ".txt", ".json", ".example", ".gitignore", ".css", ".js"}
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{2,63}$")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--name", required=True, help="Agent name, letters/digits/hyphens (used for blueprint + identity)")
    ap.add_argument("--provider", default="Demo ISV", help="ISV / provider shown in telemetry")
    ap.add_argument("--description", default="", help="One-line agent description")
    ap.add_argument("--tenant-id", default="", help="Writes a365.config.json with this tenant")
    ap.add_argument("--force", action="store_true", help="Allow a non-empty destination (never overwrites .env)")
    a = ap.parse_args()

    if not NAME_RE.match(a.name):
        print(f"Invalid --name '{a.name}': use 3-64 letters, digits or hyphens, starting with a letter.")
        return 2
    if not TEMPLATE.exists():
        print(f"Template missing: {TEMPLATE}. Run scripts/build_template.py <reference-repo> first.")
        return 2
    if a.dest.exists() and any(a.dest.iterdir()) and not a.force:
        print(f"{a.dest} is not empty. Use --force to scaffold into it anyway.")
        return 2

    tokens = {"{{AGENT_NAME}}": a.name, "{{PROVIDER_NAME}}": a.provider}
    written = 0
    for src in TEMPLATE.rglob("*"):
        if not src.is_file():
            continue
        out = a.dest / src.relative_to(TEMPLATE)
        if out.name == ".env" and out.exists():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in TEXT or src.name in TEXT:
            text = src.read_text(encoding="utf-8")
            for k, v in tokens.items():
                text = text.replace(k, v)
            out.write_bytes(text.encode("utf-8"))
        else:
            shutil.copy2(src, out)
        written += 1

    if a.description:
        env_ex = a.dest / ".env.example"
        text = env_ex.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^AGENT365_AGENT_DESCRIPTION=.*$", f"AGENT365_AGENT_DESCRIPTION={a.description}", text) \
            if "AGENT365_AGENT_DESCRIPTION=" in text else text + f"\nAGENT365_AGENT_DESCRIPTION={a.description}\n"
        env_ex.write_bytes(text.encode("utf-8"))

    if a.tenant_id:
        cfg = {
            "tenantId": a.tenant_id,
            "clientAppId": "",
            "agentIdentityDisplayName": f"{a.name} Identity",
            "agentBlueprintDisplayName": f"{a.name} Blueprint",
            "agentDescription": a.description or a.name,
            "aiTeammate": False,
            "useBlueprint": True,
        }
        (a.dest / "a365.config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    readme = a.dest / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        text = text.replace(
            "[docs/Agent365-Demo-Diagrams-clean.pptx](docs/Agent365-Demo-Diagrams-clean.pptx)",
            "`docs/Agent365-Demo-Diagrams-clean.pptx` (generated after running the deck build)",
        )
        readme.write_bytes(text.encode("utf-8"))

    leftover = [str(p) for p in a.dest.rglob("*") if p.is_file() and p.suffix in TEXT
                and "{{" in p.read_text(encoding="utf-8", errors="ignore")
                and re.search(r"\{\{[A-Z_]+\}\}", p.read_text(encoding="utf-8", errors="ignore"))]
    print(f"Scaffolded {written} files into {a.dest}")
    if leftover:
        print("WARNING unreplaced tokens in:", *leftover, sep="\n  ")
    print("Next: customize agent/tools.py + SYSTEM_PROMPT in agent/graph.py, then .\\scripts\\setup.ps1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
