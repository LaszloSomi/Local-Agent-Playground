// Build docs/Agent365-Demo-Diagrams-clean.pptx from docs/deck/build/slides.json (run export_diagrams.py first).
// Usage: node docs/deck/build_deck.js   (pptxgenjs installed globally: set NODE_PATH to `npm root -g`)
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const here = __dirname;
const data = JSON.parse(fs.readFileSync(path.join(here, "build", "slides.json"), "utf8"));
const out = path.join(here, "..", "Agent365-Demo-Diagrams-clean.pptx");

// Light palette shared with export_diagrams.py so rebuilds preserve the deck styling.
const C = {
  bg: "F7F4EF", panel: "FCFBF8", surface: "FFFFFF", text: "242424", muted: "5C5C5C",
  soft: "6F6F6F", accent: "B11F4B", accentFg: "FFFFFF", purview: "0078D4", otel: "666666",
};
const FONT = "Segoe UI";
const W = 13.333, H = 7.5, M = 0.5;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "Agent 365 Demo";
pres.title = "Agent 365 third-party agent registry demo: diagrams";

function fit(imgW, imgH, boxW, boxH) {
  const s = Math.min(boxW / imgW, boxH / imgH);
  return { w: imgW * s, h: imgH * s };
}

function header(slide, num, title) {
  slide.background = { color: C.bg };
  slide.addShape(pres.shapes.OVAL, { x: M, y: 0.38, w: 0.56, h: 0.56, fill: { color: C.accent }, line: { color: C.accent } });
  slide.addText(String(num), { x: M, y: 0.38, w: 0.56, h: 0.56, margin: 0, align: "center", valign: "middle",
    fontFace: FONT, fontSize: 20, bold: true, color: C.accentFg });
  slide.addText(title, { x: M + 0.75, y: 0.3, w: W - 2 * M - 0.75, h: 0.72, margin: 0, valign: "middle",
    fontFace: FONT, fontSize: title.length > 52 ? 24 : 28, bold: true, color: C.text });
}

function footer(slide, src) {
  if (!src) return;
  slide.addText(src, { x: M, y: H - 0.42, w: W - 2 * M, h: 0.3, margin: 0, fontFace: FONT, fontSize: 10, color: C.soft });
}

function legendRuns(legend, inline) {
  return legend.map((t, i) => {
    const isPurview = /purview/i.test(t.split(":")[0]);
    const last = i === legend.length - 1;
    return [
      { text: isPurview ? "\u25A0  " : "\u25A1  ", options: { color: isPurview ? C.purview : C.otel, bold: true } },
      { text: t + (inline && !last ? "        " : ""), options: { color: C.muted, breakLine: !inline && !last } },
    ];
  }).flat();
}

function talkSize(talk, area) {
  // Rough chars-per-square-inch budget so long talk tracks step down instead of overflowing.
  const density = talk.length / area;
  return density > 95 ? 12 : density > 70 ? 13 : 14;
}

function talkPanel(slide, x, y, w, h, talk, legend) {
  slide.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: C.panel }, line: { color: C.panel } });
  slide.addShape(pres.shapes.RECTANGLE, { x, y, w: 0.07, h, fill: { color: C.accent }, line: { color: C.accent } });
  const paras = talk.split(/\n+/).filter(Boolean);
  const legH = legend.length ? 0.32 * legend.length + 0.25 : 0;
  const textH = h - 0.3 - legH;
  const size = talkSize(talk, (w - 0.45) * (textH - 0.3));
  const runs = [{ text: "Talk track", options: { bold: true, color: C.accent, fontSize: 13, breakLine: true } }];
  paras.forEach((p, i) => runs.push({ text: p, options: { color: C.text, fontSize: size, breakLine: i < paras.length - 1 } }));
  slide.addText(runs, { x: x + 0.25, y: y + 0.15, w: w - 0.45, h: textH, valign: "top", margin: 0,
    fontFace: FONT, paraSpaceAfter: 6 });
  if (legend.length) {
    slide.addText(legendRuns(legend, false), { x: x + 0.25, y: y + h - legH - 0.1, w: w - 0.45, h: legH, margin: 0,
      valign: "bottom", fontFace: FONT, fontSize: 11 });
  }
}

// ---- Title slide -----------------------------------------------------------
{
  const s = pres.addSlide();
  s.background = { color: C.bg };
  s.addText("Agent 365", { x: M + 0.3, y: 1.3, w: 8, h: 0.6, margin: 0, fontFace: FONT, fontSize: 22, bold: true, color: C.accent });
  s.addText("Third-party agent registry demo", { x: M + 0.3, y: 1.9, w: 11, h: 1.0, margin: 0, fontFace: FONT, fontSize: 44, bold: true, color: C.text });
  s.addText("A local LangGraph agent registered in Microsoft Entra Agent ID, instrumented with the Agent 365 Observability SDK and protected by the Purview SDK.",
    { x: M + 0.3, y: 2.95, w: 10.5, h: 0.9, margin: 0, fontFace: FONT, fontSize: 18, color: C.muted });
  const steps = [["1", "Entra integration", "A365 CLI setup + MSAL OBO", C.accent],
                 ["2", "Observability SDK", "OpenTelemetry to Agent 365", C.otel],
                 ["3", "Purview SDK", "processContent DLP verdicts", C.purview]];
  const cw = 3.7, gap = 0.35, y = 4.55;
  steps.forEach(([n, t, d, col], i) => {
    const x = M + 0.3 + i * (cw + gap);
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: cw, h: 1.45, fill: { color: C.panel }, line: { color: C.panel } });
    s.addShape(pres.shapes.RECTANGLE, { x, y, w: cw, h: 0.08, fill: { color: col }, line: { color: col } });
    s.addShape(pres.shapes.OVAL, { x: x + 0.25, y: y + 0.35, w: 0.62, h: 0.62, fill: { color: col }, line: { color: col } });
    s.addText(n, { x: x + 0.25, y: y + 0.35, w: 0.62, h: 0.62, margin: 0, align: "center", valign: "middle", fontFace: FONT, fontSize: 22, bold: true, color: C.accentFg });
    s.addText(t, { x: x + 1.05, y: y + 0.3, w: cw - 1.2, h: 0.45, margin: 0, fontFace: FONT, fontSize: 17, bold: true, color: C.text });
    s.addText(d, { x: x + 1.05, y: y + 0.75, w: cw - 1.2, h: 0.4, margin: 0, fontFace: FONT, fontSize: 13, color: C.muted });
  });
  s.addText("Agents: DemoAgent1 (:8000) · DemoAgent2 (:8001) · DemoAgent3 (proposed :8002)",
    { x: M + 0.3, y: H - 0.75, w: 11, h: 0.35, margin: 0, fontFace: FONT, fontSize: 12, color: C.soft });
  s.addNotes("Agent 365 third-party agent registry demo. Build order: 1 Entra integration, 2 Observability SDK, 3 Purview SDK.");
}

// ---- Diagram slides -------------------------------------------------------
data.slides.forEach((d, idx) => {
  const s = pres.addSlide();
  const num = idx + 1;
  header(s, num, d.title);
  footer(s, d.src);
  const top = 1.15, bottom = H - 0.6, availH = bottom - top;
  const ratio = d.w / d.h;
  const hasTalk = !!d.talk;

  if (d.id === "d11") {
    // The comparison has its own labels and caveats; keep the narration in notes.
    const box = fit(d.w, d.h, W - 2 * M, availH);
    s.addImage({ path: d.png, x: (W - box.w) / 2, y: top + (availH - box.h) / 2,
      w: box.w, h: box.h, altText: d.title });
  } else if (ratio >= 1.8) {
    // Wide diagram: full width, legend line, talk strip underneath.
    const talkH = !hasTalk ? 0 : d.talk.length < 240 ? 1.15 : 1.5;
    const legH = d.legend.length ? 0.3 : 0;
    const gap = talkH ? 0.12 : 0;
    const imgH = availH - talkH - legH - gap - (legH ? 0.1 : 0);
    const box = fit(d.w, d.h, W - 2 * M, imgH);
    const x = (W - box.w) / 2;
    const y = top + (imgH - box.h) / 2;
    s.addImage({ path: d.png, x, y, w: box.w, h: box.h, altText: d.title });
    if (legH) s.addText(legendRuns(d.legend, true), { x: M, y: top + imgH + 0.05, w: W - 2 * M, h: legH, margin: 0,
      align: "center", valign: "middle", fontFace: FONT, fontSize: 12 });
    if (hasTalk) talkPanel(s, M, bottom - talkH, W - 2 * M, talkH, d.talk, []);
  } else if (hasTalk) {
    // Medium / tall diagram: image left, talk panel right.
    const panelW = ratio < 0.9 ? 5.6 : 3.9;
    const gap = 0.35;
    const imgBoxW = W - 2 * M - panelW - gap;
    const box = fit(d.w, d.h, imgBoxW, availH);
    const x = M + (imgBoxW - box.w) / 2;
    s.addImage({ path: d.png, x, y: top + (availH - box.h) / 2, w: box.w, h: box.h, altText: d.title });
    talkPanel(s, W - M - panelW, top, panelW, availH, d.talk, d.legend);
  } else {
    // No talk text: centre the diagram as large as possible.
    const box = fit(d.w, d.h, W - 2 * M, availH);
    s.addImage({ path: d.png, x: (W - box.w) / 2, y: top + (availH - box.h) / 2, w: box.w, h: box.h, altText: d.title });
  }
  s.addNotes([d.talk, d.preparation, d.legend.join("\n"), d.src].filter(Boolean).join("\n\n"));
});

pres.writeFile({ fileName: out }).then(f => console.log("wrote " + f));
