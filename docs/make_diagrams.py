#!/usr/bin/env python3
"""Generates the CortexZero diagrams (SVG) in docs/diagrams. The results chart reads the raw scored runs in
bench/scored/*.json (each held-out set scored exactly once), so it can't drift from the data.

  python3 docs/make_diagrams.py          (PNG copies in docs/diagrams/png/ need `pip install cairosvg`)
"""
from html import escape as esc
from pathlib import Path

import json

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).parent / "diagrams"
SCORED = ROOT / "bench/scored"


def acc(fname: str, config: str):
    d = json.loads((SCORED / fname).read_text())["results"]
    return d[config]["summary"]["accuracy"] if config in d else None


def false_routes(fname: str, config: str):
    return json.loads((SCORED / fname).read_text())["results"][config]["summary"]["false_routes"]
OUT.mkdir(exist_ok=True)
FONT = "font-family='DejaVu Sans, Arial, sans-serif'"
INK, MUTED, LINE, BG = "#1f2933", "#52606d", "#9aa5b1", "#ffffff"
FILL = {"code": "#e3f2fd", "model": "#fff3e0", "safety": "#ffebee", "data": "#e8f5e9", "out": "#f3e5f5", "gray": "#f5f7fa"}
EDGE = {"code": "#1e6fb8", "model": "#e07b00", "safety": "#c62828", "data": "#2e7d32", "out": "#7b1fa2", "gray": "#7b8794"}


def svg(w, h, body, title):
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}' {FONT}>"
            f"<title>{title}</title><rect width='100%' height='100%' fill='{BG}'/>"
            "<defs><marker id='a' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' orient='auto'>"
            f"<path d='M0,0 L10,5 L0,10 z' fill='{MUTED}'/></marker></defs>{body}</svg>")


def box(x, y, w, h, lines, kind="code", size=13, bold_first=True):
    out = [f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='8' fill='{FILL[kind]}' stroke='{EDGE[kind]}' stroke-width='1.5'/>"]
    n = len(lines)
    for i, t in enumerate(lines):
        ty = y + h / 2 + (i - (n - 1) / 2) * (size + 4) + size * 0.35
        wgt = "bold" if (i == 0 and bold_first) else "normal"
        col = INK if i == 0 else MUTED
        s = size if i == 0 else size - 1.5
        out.append(f"<text x='{x + w / 2}' y='{ty}' text-anchor='middle' font-size='{s}' font-weight='{wgt}' fill='{col}'>{esc(t, quote=False)}</text>")
    return "".join(out)


def arrow(x1, y1, x2, y2, label="", dx=6, dy=-6, anchor="start"):
    s = f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='{MUTED}' stroke-width='1.5' marker-end='url(#a)'/>"
    if label:
        s += f"<text x='{(x1 + x2) / 2 + dx}' y='{(y1 + y2) / 2 + dy}' font-size='11.5' fill='{MUTED}' text-anchor='{anchor}'>{esc(label, quote=False)}</text>"
    return s


def text(x, y, t, size=12, col=MUTED, anchor="start", weight="normal"):
    return f"<text x='{x}' y='{y}' font-size='{size}' fill='{col}' text-anchor='{anchor}' font-weight='{weight}'>{esc(t, quote=False)}</text>"


def legend(x, y, items):
    s = ""
    for i, (k, lab) in enumerate(items):
        s += f"<rect x='{x + i * 205}' y='{y}' width='14' height='14' rx='3' fill='{FILL[k]}' stroke='{EDGE[k]}'/>"
        s += text(x + i * 205 + 20, y + 11.5, lab, 12)
    return s


# 1. pipeline -------------------------------------------------------------------------------------------
def pipeline():
    W, MX, MW = 1120, 200, 580   # main column x / width
    b = text(W / 2, 34, "CortexZero engine: one message, end to end", 20, INK, "middle", "bold")
    b += text(W / 2, 56, "The 1B model only picks a branch id from a short list. Everything else is code and reviewed data.", 13, MUTED, "middle")
    steps = [
        ("1  Safety rules (regex on raw text, every pack)", "crisis → 988 · emergency → 911 · scam warnings · per-pack rules", "safety"),
        ("2  Consent reply?", "“yes” runs the action previewed on the turn before; consent expires after one turn", "code"),
        ("3  Normalize", "protect paths/IPs/URLs · fix typos only toward known words · keep original", "code"),
        ("4  Type-ahead index (no model)", "synonyms · stems · IDF pattern match · core-concept gate · near-miss decoys", "code"),
        ("5  Router", "clear winner → index · nothing plausible → none · else 1B picks top-3 | none | ask", "model"),
        ("6  Handler", "answer (cited) · check (read-only argv) · action (preview → consent → run → verify)", "code"),
        ("7  Evidence log (JSONL)", "question · corrections · route + candidates · commands run · answer", "data"),
    ]
    y0, h, gap = 80, 64, 22
    for i, (t, sub, k) in enumerate(steps):
        y = y0 + i * (h + gap)
        b += box(MX, y, MW, h, [t, sub], k, 14)
        if i:
            b += arrow(MX + MW / 2, y - gap, MX + MW / 2, y)
    # side exits
    exits = [(0, "SAFETY: fixed response, never routed", "safety"), (3, "NOT IN LIBRARY (near-miss / too weak)", "out"),
             (4, "NOT IN LIBRARY or ASK (\"did you mean …\")", "out"), (5, "CHECKED / DONE / NOT FIXED / ANSWER", "out")]
    for i, lab, k in exits:
        y = y0 + i * (h + gap) + h / 2
        b += arrow(MX + MW, y, MX + MW + 36, y)
        b += box(MX + MW + 38, y - 20, 320, 40, [lab], k, 12)
    b += box(10, y0 + 4 * (h + gap) - 4, 160, 72, ["1B router", "only when unsure", "LFM2.5 / Llama 3.2"], "model", 12)
    b += arrow(170, y0 + 4 * (h + gap) + 32, MX, y0 + 4 * (h + gap) + 32, "")
    b += box(10, y0 + 3 * (h + gap) - 4, 160, 72, ["Packs (YAML)", "or compiled JSON", "cz compile"], "data", 12)
    b += arrow(170, y0 + 3 * (h + gap) + 32, MX, y0 + 3 * (h + gap) + 32)
    b += box(10, y0 + 5 * (h + gap) - 4, 160, 72, ["LocalRunner", "argv only, no shell", "policy-checked"], "safety", 12)
    b += arrow(170, y0 + 5 * (h + gap) + 32, MX, y0 + 5 * (h + gap) + 32)
    H = y0 + 7 * (h + gap) + 30
    b += legend(70, H - 26, [("code", "deterministic code"), ("model", "1B model (routing only)"), ("safety", "safety boundary"), ("data", "data / log"), ("out", "what the user sees")])
    return svg(W + 50, H, b, "CortexZero pipeline")


# 2. router decision -----------------------------------------------------------------------------------
def router():
    W = 1000
    b = text(W / 2, 34, "Routing decision (czengine/router.py + index.py)", 20, INK, "middle", "bold")
    b += box(380, 60, 240, 50, ["message (normalized)", "index scores every branch"], "code")
    b += arrow(500, 110, 500, 140)
    b += box(330, 140, 340, 56, ["core gate", "branch is a candidate only if its core words appear"], "code")
    b += arrow(500, 196, 500, 226)
    rows = [
        (226, "top score < LOW (0.18)?", "none", "not in library", "out"),
        (306, "near-miss look-alike ≥ leader?", "near-miss", "not in library (topic logged)", "out"),
        (386, "clear winner? (≥0.55 & margin 0.12, or ≥0.60 & lead 0.25)", "index", "the leader branch", "code"),
        (466, "no model loaded?", "index-only", "leader if ≥ 0.60, else none", "code"),
        (546, "otherwise: 1B model chooses top-3 | none | ask", "model", "its pick (ask → leader if ≥ 0.60)", "model"),
        (626, "computer/router-only branch, but another device named?", "other-device", "not in library", "out"),
    ]
    for i, (y, q, by, res, k) in enumerate(rows):
        kk = "model" if by == "model" else "gray"
        b += box(250, y, 500, 54, [q, f"decided_by: {by}"], kk, 12.5)
        b += arrow(750, y + 27, 785, y + 27, "" if by == "model" else "yes", -14, -6)
        b += box(787, y + 7, 236, 40, [res], k, 12, False)
        if i < len(rows) - 1:
            b += arrow(500, y + 54, 500, y + 80, "no" if i < 4 else "", 6, 0)
    b += text(20, 720, "The model can only return an id from the offered list, \"none\" or \"ask\" (JSON-schema enum). Anything else = model-failed, never an answer.", 12.5)
    b += text(20, 740, "The last check runs after any branch choice: e.g. \"change the wifi password on my printer\" → not this pack's job.", 12.5)
    return svg(W + 40, 760, b, "Routing decision")


# 3. pack structure + safety layers ---------------------------------------------------------------------
def packs():
    W = 1000
    b = text(W / 2, 34, "Packs: downloadable YAML libraries, validated on load", 20, INK, "middle", "bold")
    b += box(30, 60, 300, 250, ["packs/<name>/", "pack.yaml — name, version, domain,", "status draft|unverified|reviewed,",
                                "safety_critical, sources, synonyms,", "safety rules, other_devices", "branches/*.yaml — one branch each",
                                "near-misses.yaml — out-of-scope look-alikes", "cases/dev.yaml — the pack's own tests"], "data", 12.5)
    b += box(360, 60, 300, 250, ["branch.yaml", "id, title, kind (answer|check|action)", "patterns — how people ask (≥ 2)",
                                 "core — concept words (gate)", "scope — computer|router|network|any", "text + sources (answer)",
                                 "steps argv + rules + default (check)", "risk ≥ 1 + verify step (action)"], "data", 12.5)
    b += box(690, 60, 290, 250, ["Load-time validation rejects", "shell strings · interpreters/launchers", "programs not on the check/action allowlist",
                                 "bad regex / bad fmt fields", "actions without risk or verify", "“reviewed” without reviewed_by",
                                 "safety-critical answers without sources", "`cz pack validate` adds routing lint"], "safety", 12.5)
    b += text(30, 345, "Packs", 15, INK, weight="bold")
    rows = [("home-network", "v0.2.0", "draft", "29 branches", "17 near-miss topics", "published"),
            ("safety", "v0.1.0", "draft", "0 branches", "—", "crisis 988 · emergency 911 · scam"),
            ("family-camping", "v0.1.0", "unverified", "14 branches", "safety-critical", "held back until expert review"),
            ("tower-tech", "v0.0.1", "unverified", "3 branches", "safety-critical", "held back until expert review")]
    for i, r in enumerate(rows):
        y = 360 + i * 34
        k = "safety" if "held back" in r[5] or r[0] == "safety" else "gray"
        b += f"<rect x='30' y='{y}' width='950' height='28' rx='5' fill='{FILL[k]}' stroke='{EDGE[k]}'/>"
        xs = [40, 200, 280, 380, 500, 660]
        for x, t in zip(xs, r):
            b += text(x, y + 19, t, 12.5, INK if x == 40 else MUTED, weight="bold" if x == 40 else "normal")
    b += text(30, 520, "Execution boundary (three layers)", 15, INK, weight="bold")
    layers = [("1  Pack loader", "argv[0] must be a bare program on the allowlist for the branch kind; check steps get read-only forms only"),
              ("2  Handler", "slot values typed + validated (public hosts only, ports 1-65535); chained values can’t start with “-”"),
              ("3  LocalRunner", "re-checks policy on the final argv; no shell, stdin closed, minimal env, timeout, output capped")]
    for i, (t, d) in enumerate(layers):
        y = 535 + i * 52
        b += box(30, y, 950, 44, [t, d], "safety", 13)
    return svg(W + 10, 700, b, "Pack structure and execution boundary")


# 4. results -------------------------------------------------------------------------------------------
def results():
    W, H = 1100, 600
    b = text(W / 2, 34, "Routing accuracy: honest held-out numbers (each set scored once)", 20, INK, "middle", "bold")
    b += text(W / 2, 56, "Bars = accuracy (in-scope + correct refusals). Tuning-set numbers are NOT shown here. – = not run on that set.", 13, MUTED, "middle")
    lfm = "hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF:Q4_K_M"
    cfgs = ["index-only", lfm, "llama3.2:1b", "gemma3:1b", "cz-router-lfm", "cz-router-llama"]
    runs = [("v2 · old config", "cases-heldout2-20261029T091604.json"),
            ("v3 · mid-session", "cases-heldout3-20261030T014936.json"),
            ("camping v1", "camping-heldout1-20261030T023019.json"),
            ("v4 · final engine", "cases-heldout4-20261030T042408.json")]
    groups = [(g, [acc(f, c) for c in cfgs]) for g, f in runs]
    names = ["index only", "+ LFM2.5 (untuned)", "+ llama3.2 (untuned)", "+ gemma3 (untuned)", "+ LFM2.5 FINE-TUNED", "+ llama3.2 FINE-TUNED"]
    cols = ["#1e6fb8", "#e07b00", "#7b8794", "#2e7d32", "#b45309", "#7b1fa2"]
    x0, y0, ch = 70, 90, 360
    for v in range(0, 11, 2):
        y = y0 + ch - v / 10 * ch
        b += f"<line x1='{x0}' y1='{y}' x2='{W - 20}' y2='{y}' stroke='#e4e7eb'/>" + text(x0 - 10, y + 4, f"{v / 10:.1f}", 11, MUTED, "end")
    gw = (W - 20 - x0) / 4
    for gi, (g, vals) in enumerate(groups):
        gx = x0 + gi * gw + 14
        bw = (gw - 28) / 6
        for i, v in enumerate(vals):
            x = gx + i * bw
            if v is None:
                b += text(x + bw / 2, y0 + ch - 6, "–", 12, MUTED, "middle")
                continue
            h = v * ch
            b += f"<rect x='{x + 2}' y='{y0 + ch - h}' width='{bw - 4}' height='{h}' rx='2' fill='{cols[i]}'/>"
            b += text(x + bw / 2, y0 + ch - h - 5, f"{v:.2f}", 10, INK, "middle")
        b += text(gx + (gw - 28) / 2, y0 + ch + 22, g, 13, INK, "middle", "bold")
    for i, n in enumerate(names):
        cx = 70 + (i % 3) * 340
        cy = H - 110 + (i // 3) * 22
        b += f"<rect x='{cx}' y='{cy}' width='14' height='14' rx='3' fill='{cols[i]}'/>" + text(cx + 20, cy + 12, n, 12.5)
    v4 = "cases-heldout4-20261030T042408.json"
    b += text(30, H - 40, f"v4 false routes (of 97): index-only {false_routes(v4, 'index-only')} → fine-tuned llama "
              f"{false_routes(v4, 'cz-router-llama')}. Raw runs: bench/scored", 12.5)
    b += text(30, H - 20, "Fine-tuned routers: LoRA on 910 examples (no held-out data), exported as Q4_K_M GGUF. Training cost $0.37 on RunPod.", 12.5)
    return svg(W, H, b, "Routing results")


# 5. work process --------------------------------------------------------------------------------------
def process():
    W = 1000
    b = text(W / 2, 34, "How the numbers were produced (only code-checked results count)", 20, INK, "middle", "bold")
    b += box(30, 70, 200, 70, ["Hermes czcasewriter", "writes NEW messages", "(never sees labels)"], "model", 12.5)
    b += box(290, 60, 200, 40, ["czlabela (nemotron)"], "model", 12.5)
    b += box(290, 110, 200, 40, ["czlabelb (gpt-oss-20b)"], "model", 12.5)
    b += arrow(230, 95, 290, 80) + arrow(230, 105, 290, 130)
    b += box(550, 70, 200, 70, ["agreement filter", "keep only messages both", "labellers agreed on"], "code", 12)
    b += arrow(490, 80, 550, 95) + arrow(490, 130, 550, 115)
    b += box(800, 70, 180, 70, ["score ONCE", "index + 3 local 1Bs", "→ RESULTS.md"], "data", 12.5)
    b += arrow(750, 105, 800, 105)
    b += arrow(890, 140, 890, 190) + box(760, 190, 220, 54, ["then it becomes a dev set", "(tuning allowed only now)"], "gray", 12.5)
    b += box(30, 200, 330, 70, ["Codex: independent code review", "17 findings → each reproduced on disk", "16 fixed + regression tests"], "safety", 12.5)
    b += box(390, 200, 330, 70, ["Hermes czredteam: 80 attack messages", "real misses fixed; nonsense labels ignored", "misroutes refused 14/35 → 27/35"], "safety", 12.5)
    b += text(30, 310, "Held-out sets so far: v2 → dev2, v3 → dev3, camping v1 → camping dev2, v4 → dev4 (each scored once, then used for tuning).", 12.5)
    b += text(30, 330, "Writers and labellers never see each other's output or the engine's answers. Held-out sets are never used for tuning before their one scoring.", 12.5)
    return svg(W, 350, b, "Evaluation process")


def to_png(svg_path: Path) -> None:
    """PNG copy for places that don't show SVG well (README on some clients, slides). Needs `pip install cairosvg`."""
    try:
        import cairosvg
    except ImportError:
        print("  (skipping PNG: pip install cairosvg)")
        return
    out = OUT / "png" / (svg_path.stem + ".png")
    out.parent.mkdir(exist_ok=True)
    cairosvg.svg2png(url=str(svg_path), write_to=str(out), scale=2, background_color="white")
    print("wrote", out)


for name, fn in [("1-pipeline", pipeline), ("2-routing-decision", router), ("3-packs-and-execution-boundary", packs),
                 ("4-results-heldout", results), ("5-evaluation-process", process)]:
    (OUT / f"{name}.svg").write_text(fn(), encoding="utf-8")
    print("wrote", OUT / f"{name}.svg")
    to_png(OUT / f"{name}.svg")
