#!/usr/bin/env python3
"""Build router fine-tuning data (chat JSONL) from labelled cases + the packs themselves.

Each example is EXACTLY what the engine sends the 1B router at run time (czengine.model.router_prompt):
the message plus the index's top-3 candidates (shuffled), target {"choice": "<branch id | none>"}.
Sources: case files you name (dev sets, Hermes TRAINING messages), every branch pattern (in-scope),
every near-miss phrasing (none). Held-out files and the owner's private messages are refused.

  make_router_data.py OUT.jsonl CASES.yaml [CASES.yaml ...] [--val-frac 0.1] [--seed 7] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from czengine.index import Index  # noqa: E402
from czengine.library import load_packs  # noqa: E402
from czengine.model import router_prompt  # noqa: E402
from czengine.normalize import Normalizer  # noqa: E402

FORBIDDEN = ("heldout", "held-out", "own-questions")
K = 3


def example(idx: Index, norm: Normalizer, text: str, expect: list[str], rng: random.Random) -> dict | None:
    clean = norm(text).text
    cands = [c.branch for c in idx.search(clean, k=6)]
    if not cands:  # the router never sees these (index says none on its own)
        cands = []
    target = "none"
    if expect != ["none"]:
        hit = next((b for b in cands[:K] if b.id in expect), None)
        if hit is None:
            hit = next(b for b in idx.branches if b.id == expect[0])
            cands = [c for c in cands if c.id != hit.id][: K - 1] + [hit]
        target = hit.id
    opts = cands[:K]
    if len(opts) < 2:  # pad with random other branches so every prompt offers 2-3 choices
        pool = [b for b in idx.branches if b not in opts and b.id not in expect]
        opts += rng.sample(pool, K - len(opts))
    rng.shuffle(opts)
    prompt, _, _ = router_prompt(clean, [(b.id, f"{b.title} — {b.description}") for b in opts])
    return {"messages": [{"role": "user", "content": prompt},
                         {"role": "assistant", "content": json.dumps({"choice": target})}],
            "meta": {"text": text, "target": target}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("cases", nargs="+", type=Path)
    ap.add_argument("--packs", nargs="*", default=[str(ROOT / "packs/home-network"), str(ROOT / "packs/safety")])
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--limit", type=int, default=0, help="keep only N examples (CPU dry runs)")
    a = ap.parse_args()
    for c in a.cases:
        if any(f in str(c.resolve()).lower() for f in FORBIDDEN):
            raise SystemExit(f"refusing {c}: held-out sets and private messages never go into training data")
    rng = random.Random(a.seed)
    packs = load_packs([Path(p) for p in a.packs])
    idx = Index(packs)
    norm = Normalizer(idx.vocabulary)
    items: list[tuple[str, list[str], str]] = []
    for c in a.cases:
        for x in yaml.safe_load(c.read_text()):
            items.append((x["report"], list(x["expect"]), c.name))
    for b in idx.branches:
        items += [(p, [b.id], "pattern") for p in b.patterns]
    for p in packs:
        for topic, pats in p.near_misses.items():
            items += [(t, ["none"], f"near-miss:{topic}") for t in pats]
    rows = [r for r in (example(idx, norm, t, e, rng) for t, e, _ in items) if r]
    for r, (_, _, src) in zip(rows, items):
        r["meta"]["source"] = src
    rng.shuffle(rows)
    if a.limit:
        rows = rows[: a.limit]
    n_val = int(len(rows) * a.val_frac)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    for name, part in (("train", rows[n_val:]), ("val", rows[:n_val])):
        path = a.out.with_name(a.out.stem + f".{name}.jsonl")
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in part))
        none = sum(r["meta"]["target"] == "none" for r in part)
        print(f"{path}: {len(part)} examples ({none} none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
