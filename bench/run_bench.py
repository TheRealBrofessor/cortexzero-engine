#!/usr/bin/env python3
"""Routing benchmark: index-only vs index + each 1B router model, on a labelled case file.

Only ROUTING is scored here (handlers are not executed). Metrics per configuration:
accuracy, in-scope accuracy, correct-"none" rate, false routes (wrong branch or routed an
out-of-scope question), model-call rate, mean/max routing latency.

  run_bench.py bench/cases-dev.yaml --models gemma3:1b llama3.2:1b <lfm-tag> [--out bench/results]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from czengine.index import Index  # noqa: E402
from czengine.library import load_packs  # noqa: E402
from czengine.model import OllamaRouterModel, OpenAICompatRouterModel  # noqa: E402
from czengine.normalize import Normalizer  # noqa: E402
from czengine.router import Router  # noqa: E402


def score(rows: list[dict]) -> dict:
    n = len(rows)
    ins = [r for r in rows if r["expect"] != ["none"]]
    out = [r for r in rows if r["expect"] == ["none"]]
    ok = lambda r: (r["got"] or "none") in r["expect"]
    lat = [r["seconds"] for r in rows]
    return {
        "cases": n, "accuracy": round(sum(map(ok, rows)) / n, 3),
        "in_scope": f"{sum(map(ok, ins))}/{len(ins)}", "none_correct": f"{sum(map(ok, out))}/{len(out)}",
        "false_routes": sum(1 for r in rows if r["got"] and r["got"] not in r["expect"]),
        "asked": sum(1 for r in rows if r["decided_by"] == "ask"),
        "model_failed": sum(1 for r in rows if r["decided_by"] == "model-failed"),
        "model_calls": sum(1 for r in rows if r["decided_by"] in ("model", "ask", "none-model", "model-failed")),
        "latency_mean_s": round(statistics.mean(lat), 3), "latency_max_s": round(max(lat), 3),
    }


def run(router: Router, norm: Normalizer, cases: list[dict]) -> list[dict]:
    rows = []
    for c in cases:
        t0 = time.monotonic()
        rt = router.route(norm(c["report"]).text)
        by = "none-model" if rt.decided_by == "none" and rt.model_seconds else rt.decided_by
        rows.append({"id": c["id"], "report": c["report"], "expect": c["expect"], "got": rt.branch_id,
                     "decided_by": by, "top": [(x.branch.id, x.score) for x in rt.candidates[:3]],
                     "model_raw": rt.model_raw, "seconds": round(time.monotonic() - t0, 3)})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", type=Path)
    ap.add_argument("--models", nargs="*", default=[])
    ap.add_argument("--packs", nargs="*", default=[str(ROOT / "packs/home-network"), str(ROOT / "packs/safety")])
    ap.add_argument("--out", type=Path, default=ROOT / "bench/results")
    ap.add_argument("--backend", choices=["ollama", "openai"], default="ollama")
    ap.add_argument("--url", default=None)
    a = ap.parse_args()
    cases = yaml.safe_load(a.cases.read_text())
    packs = load_packs([Path(p) for p in a.packs])
    index = Index(packs)
    norm = Normalizer(index.vocabulary)
    make = ((lambda m: OpenAICompatRouterModel(m, a.url)) if a.backend == "openai"
            else (lambda m: OllamaRouterModel(m, a.url or "http://127.0.0.1:11434")))
    configs = [("index-only", None)] + [(m, make(m)) for m in a.models]
    report = {"cases_file": str(a.cases), "branches": len(index.branches), "results": {}}
    for name, model in configs:
        if model is not None:  # warm the model so load time isn't counted as routing latency
            model.choose("warm up", [("x.y", "test")])
        rows = run(Router(index, model), norm, cases)
        report["results"][name] = {"summary": score(rows), "rows": rows}
        s = report["results"][name]["summary"]
        print(f"{name:42} acc {s['accuracy']:.3f} | in-scope {s['in_scope']:>6} | none {s['none_correct']:>6} | "
              f"false {s['false_routes']:2} | ask {s['asked']:2} | model calls {s['model_calls']:2} | "
              f"fail {s['model_failed']} | mean {s['latency_mean_s']}s max {s['latency_max_s']}s", flush=True)
    a.out.mkdir(parents=True, exist_ok=True)
    path = a.out / f"{a.cases.stem}-{time.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print("->", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
