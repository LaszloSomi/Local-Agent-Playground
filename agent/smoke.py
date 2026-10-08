"""CLI smoke test: runs one turn through the same InvokeAgentScope path as the web app.

Usage:  .venv\\Scripts\\python -m agent.smoke "What's the weather in San Francisco?"
"""
import sys

from fastapi.testclient import TestClient

from agent.app import app


def main():
    prompt = " ".join(sys.argv[1:]) or "What's the weather in San Francisco?"
    with TestClient(app) as c:
        r = c.post("/api/chat", json={"message": prompt})
        print(r.status_code, r.json())


if __name__ == "__main__":
    main()
