"""The CortexZero engine: input -> safety -> route -> handler -> answer, with an evidence record.

The model (if any) only routes. Everything it could affect is a branch id from a fixed set.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from czengine.handlers import Handlers, Outcome
from czengine.index import Index
from czengine.library import Pack, load_packs
from czengine.normalize import Normalizer
from czengine.router import Route, Router

REPLY_YES = re.compile(r"^\s*(y|yes|yeah|yep|do it|go|go ahead|ok|okay|sure|run it)\s*[.!]*\s*$", re.I)


@dataclass
class Turn:
    question: str
    cleaned: str
    corrections: dict
    route: dict
    outcome: dict | None
    label: str                     # ✓ checked/done · 📖 answer · ⚠ safety · ∅ not in library
    answer: str
    seconds: float
    ts: float = field(default_factory=time.time)


class Engine:
    def __init__(self, packs: list[Pack], model=None, evidence: Path | None = None, runner=None,
                 index: Index | None = None, online=None):
        self.packs = packs
        self.index = index or Index(packs)
        self.normalize = Normalizer(self.index.vocabulary)
        self.router = Router(self.index, model)
        self.handlers = Handlers(runner, online)
        self.safety = [r for p in packs for r in p.safety]
        self.by_id = {b.id: b for b in self.index.branches}
        self.pack_of = {p.name: p for p in packs}
        self.evidence = evidence
        self.last_branch: str | None = None
        self.pending: tuple[str, str] | None = None   # (branch_id, original text) awaiting consent

    @classmethod
    def from_dirs(cls, dirs: list[Path], compiled: Path | None = None, **kw) -> "Engine":
        """Use the precompiled packs if `compiled` is current for exactly these dirs, else the YAML."""
        if compiled is not None:
            from czengine.compiled import load_compiled
            hit = load_compiled(dirs, compiled)
            if hit:
                packs, index = hit
                eng = cls(packs, index=index, **kw)
                eng.compiled = True
                return eng
        eng = cls(load_packs(dirs), **kw)
        eng.compiled = False
        return eng

    def suggest(self, partial: str, k: int = 3) -> list[str]:
        return [c.branch.title for c in self.index.search(partial, k=k, prefix=True)]

    def ask(self, question: str) -> Turn:
        t0 = time.monotonic()
        pending, self.pending = self.pending, None   # consent is good for the very next turn only
        norm = self.normalize(question)
        # 1. safety first, on the raw text: never routed, never model-dependent
        for rule in self.safety:
            if rule.pattern.search(question):
                return self._log(Turn(question, norm.text, norm.corrections, {"decided_by": "safety", "rule": rule.id},
                                      None, "⚠ safety", rule.response.strip(), time.monotonic() - t0))
        # 2. consent reply to a pending action
        if pending and REPLY_YES.match(question):
            bid, orig = pending
            out = self.handlers.handle(self.by_id[bid], orig, consent=True)
            return self._finish(question, norm, {"decided_by": "consent", "branch_id": bid}, out, t0)
        # 3. route (index, then 1B for the ambiguous middle)
        route = self.router.route(norm.text, context=self.by_id[self.last_branch].title if self.last_branch else "")
        rd = {"branch_id": route.branch_id, "decided_by": route.decided_by,
              "candidates": [(c.branch.id, c.score) for c in route.candidates[:3]],
              "model_seconds": round(route.model_seconds, 3)}
        if route.branch_id is None:
            if route.decided_by == "ask" and route.candidates:
                opts = "; ".join(c.branch.title for c in route.candidates[:3])
                msg = f"Can you say a bit more? Did you mean: {opts}?"
            else:
                msg = "That's not in my library yet. I won't guess. (Logged so it can be added.)"
            return self._log(Turn(question, norm.text, norm.corrections, rd, None, "∅ not in library",
                                  msg, time.monotonic() - t0))
        out = self.handlers.handle(self.by_id[route.branch_id], question)
        return self._finish(question, norm, rd, out, t0)

    def _finish(self, question, norm, rd, out: Outcome, t0) -> Turn:
        self.last_branch = out.branch_id
        if out.status == "needs_consent":
            self.pending = (out.branch_id, question)
        label = {"answer": "📖 library answer", "check": "✓ checked live", "action": "✓ action"}[out.kind]
        if out.status in ("verify_failed", "error"):
            label = "⚠ not fixed"
        if out.status == "needs_consent":
            label = "? needs your OK"
        text = out.message + ("\n" + "\n".join(f"  $ {c}" for c in out.preview) if out.preview else "")
        pack = self.pack_of.get(self.by_id[out.branch_id].pack)
        if pack and pack.safety_critical and pack.status != "reviewed":
            text += f"\n(Draft: the '{pack.name}' pack is {pack.status} — not yet reviewed by a person. Check the sources.)"
        return self._log(Turn(question, norm.text, norm.corrections, rd, asdict(out), label, text,
                              time.monotonic() - t0))

    def _log(self, turn: Turn) -> Turn:
        if self.evidence:
            self.evidence.parent.mkdir(parents=True, exist_ok=True)
            with self.evidence.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(turn), ensure_ascii=False, default=str) + "\n")
        return turn
