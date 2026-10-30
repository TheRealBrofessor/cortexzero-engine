"""Pack compiler: YAML packs -> one precompiled JSON (packs + index tables) for fast startup.

`cz compile` writes it; the engine uses it only if every source file still has the same hash,
otherwise it falls back to the YAML (and says so). The JSON is derived data, never edited by hand.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from czengine.index import Index
from czengine.library import Branch, Pack, Rule, SafetyRule, Step, load_packs

FORMAT = 1


def source_files(dirs: list[Path]) -> list[Path]:
    files = []
    for d in dirs:
        files += [d / "pack.yaml", *sorted((d / "branches").glob("*.yaml"))]
        if (d / "near-misses.yaml").is_file():
            files.append(d / "near-misses.yaml")
    return [f for f in files if f.is_file()]


def fingerprint(dirs: list[Path]) -> dict[str, str]:
    return {str(f.resolve()): hashlib.sha256(f.read_bytes()).hexdigest() for f in source_files(dirs)}


def _pack_to_dict(p: Pack) -> dict:
    d = asdict(p)
    d["safety"] = [{"id": r.id, "pattern": r.pattern.pattern, "response": r.response} for r in p.safety]
    return d


def _pack_from_dict(d: dict) -> Pack:
    def step(s):
        return Step(**s) if s else None
    branches = []
    for b in d["branches"]:
        b = dict(b)
        b["steps"] = [step(s) for s in b["steps"]]
        b["rules"] = [Rule(**r) for r in b["rules"]]
        b["verify"] = step(b["verify"])
        branches.append(Branch(**b))
    d = dict(d, branches=branches,
             safety=[SafetyRule(r["id"], re.compile(r["pattern"], re.I), r["response"]) for r in d["safety"]])
    return Pack(**d)


def compile_packs(dirs: list[Path], out: Path) -> dict:
    packs = load_packs(dirs)
    index = Index(packs)
    data = {"format": FORMAT, "sources": fingerprint(dirs), "packs": [_pack_to_dict(p) for p in packs],
            "index": index.to_state()}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"packs": len(packs), "branches": len(index.branches), "terms": len(index.idf), "bytes": out.stat().st_size}


def load_compiled(dirs: list[Path], path: Path) -> tuple[list[Pack], Index] | None:
    """Packs + index from the compiled file, or None if it is missing, another format, or stale."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("format") != FORMAT or data.get("sources") != fingerprint(dirs):
        return None
    packs = [_pack_from_dict(p) for p in data["packs"]]
    return packs, Index.from_state(packs, data["index"])
