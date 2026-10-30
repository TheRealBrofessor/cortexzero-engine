"""Pack tools: scaffold a new pack, lint (validate) packs beyond what loading enforces, list, tree."""
from __future__ import annotations

from pathlib import Path

import yaml

from czengine.index import Index
from czengine.library import ID_RE, Pack, PackError, load_pack, load_packs

PACK_TEMPLATE = """name: {name}
version: 0.1.0
domain: {domain}
status: draft          # draft | unverified | reviewed (reviewed needs reviewed_by)
safety_critical: false # true: every answer branch needs cited sources, pack-level sources required
description: {description}
sources: []
synonyms: {{}}
"""

BRANCH_TEMPLATE = """id: {prefix}.example
title: Example answer
kind: answer           # answer | check | action
description: One line that says what this branch does.
patterns:              # how people actually ask (at least 2; more = better routing)
- how do i do the example thing
- example thing is not working
core:                  # the branch can only win if one of these words is in the message
- [example]
text: |
  Replace this with the answer.
sources: []
"""

NEAR_TEMPLATE = """# Out-of-scope look-alikes: messages that share words with this pack but need something else.
# topic:
# - phrasing
"""


def new_pack(name: str, parent: Path, domain: str = "", description: str = "") -> Path:
    if not ID_RE.match(f"x.{name}"):
        raise PackError(f"bad pack name {name!r}: use lower-case letters, digits, dashes")
    root = parent / name
    if root.exists():
        raise PackError(f"{root} already exists")
    (root / "branches").mkdir(parents=True)
    (root / "cases").mkdir()
    (root / "pack.yaml").write_text(PACK_TEMPLATE.format(name=name, domain=domain or name,
                                                         description=description or f"{name} help"))
    prefix = name.split("-")[0][:8]
    (root / "branches/example.yaml").write_text(BRANCH_TEMPLATE.format(prefix=prefix))
    (root / "near-misses.yaml").write_text(NEAR_TEMPLATE)
    (root / "cases/dev.yaml").write_text("# - id: d001\n#   report: how do i do the example thing\n#   expect: [x.example]\n")
    return root


def lint(dirs: list[Path]) -> tuple[list[str], list[str]]:
    """(errors, warnings). Errors: the packs do not load. Warnings: routing quality problems."""
    errors, warns = [], []
    packs: list[Pack] = []
    for d in dirs:
        try:
            packs.append(load_pack(d))
        except (PackError, OSError, yaml.YAMLError, KeyError, TypeError, ValueError) as e:
            errors.append(f"{d}: {e}")
    if errors:
        return errors, warns
    try:
        load_packs(dirs)
    except PackError as e:
        return [str(e)], warns
    idx = Index(packs)
    seen: dict[tuple, str] = {}
    for p in packs:
        if p.safety_critical and p.status == "draft":
            warns.append(f"{p.name}: safety-critical pack is still 'draft' (set 'unverified' until a person reviews it)")
        for b in p.branches:
            if not b.core:
                warns.append(f"{b.id}: no core words (any shared word can route here)")
            for g in b.core:
                for w in g:
                    if w not in idx.conflicts and idx.terms(w) != [idx.canon.get(w, w)]:
                        warns.append(f"{b.id}: core word {w!r} can never match (filler or punctuation)")
            if len(b.patterns) < 5:
                warns.append(f"{b.id}: only {len(b.patterns)} patterns (5+ recommended)")
            for pat in b.patterns:
                key = tuple(sorted(set(idx.terms(pat))))
                if key in seen and seen[key] != b.id:
                    warns.append(f"{b.id}: pattern {pat!r} has the same words as one in {seen[key]}")
                seen.setdefault(key, b.id)
        for topic, pats in p.near_misses.items():
            for pat in pats:
                key = tuple(sorted(set(idx.terms(pat))))
                if key in seen:
                    errors.append(f"{p.name}: near-miss {topic}: {pat!r} has the same words as a pattern of {seen[key]}")
    from czengine.router import Router
    router = Router(idx)  # index-only, the same thresholds the engine uses
    for p in packs:
        for b in p.branches:  # every branch must still win its own patterns
            for pat in b.patterns:
                r = router.route(pat)
                if r.branch_id != b.id:
                    why = f"near-miss {r.model_raw}" if r.decided_by == "near-miss" else (r.branch_id or r.decided_by)
                    warns.append(f"{b.id}: own pattern {pat!r} routes to {why}")
    return errors, warns


def describe(dirs: list[Path]) -> list[dict]:
    out = []
    for d in dirs:
        try:
            p = load_pack(d)
            out.append({"name": p.name, "version": p.version, "domain": p.domain, "status": p.status,
                        "safety_critical": p.safety_critical, "branches": len(p.branches),
                        "near_miss_topics": len(p.near_misses), "safety_rules": len(p.safety), "path": str(d)})
        except Exception as e:  # list must still show broken packs
            out.append({"name": d.name, "error": str(e), "path": str(d)})
    return out


def tree(packs: list[Pack]) -> str:
    lines = []
    for p in packs:
        lines.append(f"{p.name} v{p.version} [{p.status}] — {p.description}")
        if p.safety:
            lines.append(f"├─ safety rules (checked first): {', '.join(r.id for r in p.safety)}")
        groups: dict[str, list] = {}
        for b in p.branches:
            groups.setdefault(b.kind, []).append(b)
        kinds = [k for k in ("answer", "check", "action") if k in groups]
        for i, k in enumerate(kinds):
            last = i == len(kinds) - 1 and not p.near_misses
            lines.append(f"{'└─' if last else '├─'} {k}")
            for j, b in enumerate(groups[k]):
                pre = "   " if last else "│  "
                tail = f" (risk {b.risk})" if b.risk else ""
                lines.append(f"{pre}{'└─' if j == len(groups[k]) - 1 else '├─'} {b.id}: {b.title}{tail}")
        if p.near_misses:
            lines.append(f"└─ near-miss topics (→ not in library): {', '.join(p.near_misses)}")
    return "\n".join(lines)
