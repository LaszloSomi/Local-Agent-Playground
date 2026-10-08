"""Check Git's candidate files locally; never print credential values or contact cloud services."""
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_KEY = re.compile(r"SECRET|TOKEN|TENANT|CLIENT_ID|AGENT_ID|BLUEPRINT_ID|REGISTRATION_ID")
PATTERNS = {
    "access token": re.compile(r"eyJ[\w-]{15,}\.[\w-]{15,}\.[\w-]{10,}"),
    "GitHub token": re.compile(r"(?:ghp_|github_pat_|gho_)[A-Za-z0-9_]{20,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def git(git_dir, *args):
    return subprocess.check_output(
        ["git", f"--git-dir={git_dir}", f"--work-tree={ROOT}", *args], cwd=ROOT
    )


def private_values():
    values = set()
    for path in ROOT.glob(".env*"):
        if path.name.endswith(".example") or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("'\"")
            if PRIVATE_KEY.search(key) and len(value) >= 8:
                values.add(value)
                if re.fullmatch(r"[0-9a-fA-F-]{36}", value):
                    values.add(value[:8] + "-")
    for name in ("a365.config.json", "a365.generated.config.json"):
        path = ROOT / name
        if path.exists():
            # IDs are not credentials, but publishing the operator's IDs is unnecessary.
            values.update(re.findall(
                r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b",
                path.read_text(encoding="utf-8-sig"),
            ))
    return values


def main():
    errors = []
    private = private_values()
    with tempfile.TemporaryDirectory(prefix="agent-publish-") as directory:
        git_dir = Path(directory) / "metadata.git"
        subprocess.run(["git", "init", "--bare", "--quiet", str(git_dir)], check=True)
        # Temporary metadata checks the current folder even before git init.
        candidates = [p for p in git(git_dir, "ls-files", "--others", "--exclude-standard", "-z")
                      .decode().split("\0") if p]
        must_ignore = [
            ".env", ".env.agent2", ".env.harness-backup", "a365.config.json",
            "a365.generated.config.json", "appsettings.json", "agent2.log",
            ".venv/probe", "tools/probe", "docs/deck/build/probe",
            "docs/planning/probe", "docs/~$deck.pptx", "credentials.pfx",
            "session.har", "msal_cache.json",
            "docs/Agent365-Demo-Diagrams.pptx",
            "docs/Agent365-Demo-Diagrams-Public.pptx",
        ]
        for name in must_ignore:
            result = subprocess.run(
                ["git", f"--git-dir={git_dir}", f"--work-tree={ROOT}", "check-ignore", "-q", name],
                cwd=ROOT,
            )
            if result.returncode != 0:
                errors.append((name, "required ignore rule missing"))
        for name in candidates:
            path = ROOT / name
            if path.suffix == ".pptx":
                with zipfile.ZipFile(path) as archive:
                    text = "\n".join(archive.read(n).decode("utf-8") for n in archive.namelist()
                                     if n.endswith(".xml"))
            else:
                try:
                    text = path.read_text(encoding="utf-8-sig")
                except UnicodeDecodeError:
                    errors.append((name, "binary file requires manual privacy review"))
                    continue
            if any(value.lower() in text.lower() for value in private):
                errors.append((name, "contains a local credential or tenant identifier"))
            for label, pattern in PATTERNS.items():
                if pattern.search(text):
                    errors.append((name, label))
            if re.search(r"[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b|<)[\w.-]+", text):
                errors.append((name, "operator-specific Windows profile path"))
            if path.name.endswith(".example"):
                for line in text.splitlines():
                    if line.lstrip().startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    value = value.strip().strip("'\"")
                    if re.search(r"SECRET|TOKEN", key) and value and value not in {
                        "change-me-to-a-long-random-string",
                    } and not (value.startswith("<") and value.endswith(">")):
                        errors.append((name, "non-placeholder credential in example configuration"))
        if (ROOT / ".git").exists():
            tracked = subprocess.check_output(
                ["git", "ls-files", "-ci", "--exclude-standard", "-z"], cwd=ROOT
            ).decode().split("\0")
            errors.extend((name, "already tracked despite ignore rule") for name in tracked if name)
    for name, reason in errors:
        print(f"FAIL {name}: {reason}")
    print(f"Checked {len(candidates)} publication candidates; {len(errors)} issue(s).")
    print("Heuristic check only: review the staged diff and images before publishing. No files uploaded.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
