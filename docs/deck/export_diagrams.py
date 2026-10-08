"""Render every diagram in docs/00-diagrams.html to PNG (light theme) and dump slide text to JSON.

Usage: python docs/deck/export_diagrams.py   (serves the repo on a free port itself; uses installed Microsoft Edge)
"""
import json
import pathlib
import threading
import functools
import http.server
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = pathlib.Path(__file__).resolve().parent / "build"
OUT.mkdir(exist_ok=True)

# Deck-specific light palette, independent of the HTML page's selected theme.
LIGHTER = """
html[data-theme="light"] {
  --cp-bg: #f7f4ef;
  --cp-bg-elevated: #fcfbf8;
  --cp-surface: #ffffff;
  --cp-surface-soft: #f5f5f5;
  --cp-border: #dedede;
  --cp-border-strong: #666666;
  --cp-text: #242424;
  --cp-text-muted: #5c5c5c;
  --cp-text-soft: #6f6f6f;
  --cp-accent: #b11f4b;
  --cp-accent-soft: rgba(177, 31, 75, 0.08);
  --cp-accent-fg: #ffffff;
}
.diagram .flowchart-link { stroke: var(--cp-border-strong) !important; stroke-width: 2px !important; }
.diagram marker path { fill: var(--cp-border-strong) !important; stroke: var(--cp-border-strong) !important; }
header { display: none !important; }
"""

# Tall top-down flowcharts don't fit a 16:9 slide; lay them out left-to-right in the deck only.
DECK_DIRECTION = {"d5": "LR", "d9": "LR", "d10": "LR"}


def patch_html(html):
    for sid, direction in DECK_DIRECTION.items():
        i = html.index(f'id="{sid}"')
        j = html.index("</pre>", i)
        html = html[:i] + html[i:j].replace("flowchart TB", f"flowchart {direction}", 1) + html[j:]
    # Sanitize before rendering: names embedded in PNGs cannot be cleaned through PPTX XML.
    return html.replace("Laszlo-AgentRegistryDemo2 Agent", "DemoAgent2").replace(
        "Laszlo-AgentRegistryDemo", "DemoAgent"
    ).replace("Laszlo", "Demo")


EXTRACT = """
() => [...document.querySelectorAll('section.card')].map(s => ({
  id: s.id,
  title: (s.querySelector('h2')?.innerText || '').replace(/^\\d+\\s*/, '').trim(),
  talk: (s.querySelector('.talk')?.innerText.trim() || '').replace(/^talk track:\\s*/i, ''),
  preparation: s.querySelector('.prep')?.innerText.trim() || '',
  legend: [...s.querySelectorAll('.legend span')].map(x => x.innerText.trim()),
  src: s.querySelector('.src')?.innerText.trim() || '',
}))
"""


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    httpd = serve()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/docs/00-diagrams.html?scoutTheme=light"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1500, "height": 1800}, device_scale_factor=2)
        def _patch(route):
            resp = route.fetch()
            route.fulfill(response=resp, body=patch_html(resp.text()), headers={"content-type": "text/html; charset=utf-8"})

        page.route("**/docs/00-diagrams.html*", _patch)
        page.goto(url)
        page.wait_for_function("document.querySelectorAll('pre.mermaid').length === 0", timeout=60000)
        page.evaluate("document.documentElement.setAttribute('data-theme','light')")
        page.add_style_tag(content=LIGHTER)
        page.evaluate("async () => { await window.toggleTheme(); await window.toggleTheme(); }")
        page.wait_for_function("document.querySelectorAll('pre.mermaid').length === 0 && document.documentElement.getAttribute('data-theme')==='light'")
        page.wait_for_timeout(800)
        if page.locator(".err").count():
            raise RuntimeError(page.locator(".err").all_inner_texts())
        assert page.locator("#d11 table").count() == 1, "Comparison must survive theme toggles"
        meta = page.evaluate(EXTRACT)
        bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        for m in meta:
            svg = page.locator(f"#{m['id']} .diagram svg")
            el = svg if svg.count() else page.locator(f"#{m['id']} .diagram")
            path = OUT / f"{m['id']}.png"
            el.screenshot(path=str(path), omit_background=True)
            box = el.bounding_box()
            m["png"] = str(path)
            m["w"], m["h"] = box["width"], box["height"]
            print(m["id"], round(box["width"]), "x", round(box["height"]), m["title"])
        (OUT / "slides.json").write_text(json.dumps({"bg": bg, "slides": meta}, indent=1, ensure_ascii=False), encoding="utf-8")
        browser.close()
    httpd.shutdown()


if __name__ == "__main__":
    main()
