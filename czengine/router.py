"""Routing decision: index first, the 1B model only for the ambiguous middle.

clear winner (score >= HIGH and margin >= MARGIN)  -> index decides, no model
nothing plausible (top < LOW)                       -> none, no model
a near-miss look-alike scores >= the leader         -> none ("near-miss"), no model
otherwise                                           -> model picks among the top K, or none / ask
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from czengine.index import Candidate, Index

HIGH, MARGIN, LOW, K = 0.55, 0.12, 0.18, 3
ACCEPT = 0.60   # below this the index leader is not trusted on its own (tuned on dev + dev2)
NEAR_MARGIN = 0.0  # a near-miss look-alike within this of the leader means "none" (tuned on dev + dev2)
LEAD = 0.25     # a leader above ACCEPT this far ahead of #2 is also a clear winner (tuned on dev only)


@dataclass
class Route:
    branch_id: str | None          # None = out of library
    decided_by: str                # index | model | none | ask | model-failed
    candidates: list[Candidate] = field(default_factory=list)
    model_seconds: float = 0.0
    model_raw: str = ""
    seconds: float = 0.0


class Router:
    def __init__(self, index: Index, model=None, high=HIGH, margin=MARGIN, low=LOW, k=K, accept=ACCEPT):
        self.index, self.model = index, model
        self.high, self.margin, self.low, self.k, self.accept = high, margin, low, k, accept

    def route(self, text: str, context: str = "") -> Route:
        r = self._route(text, context)
        if r.branch_id:
            b = next(c.branch for c in r.candidates if c.branch.id == r.branch_id) if any(
                c.branch.id == r.branch_id for c in r.candidates) else None
            dev = self.index.other_device(text, b) if b else None
            if dev:  # e.g. "the wifi password on my printer": this pack can't change the printer
                return Route(None, "other-device", r.candidates, r.model_seconds, dev, r.seconds)
        return r

    def _route(self, text: str, context: str = "") -> Route:
        t0 = time.monotonic()
        cands = self.index.search(text, k=max(self.k, 5))
        top = cands[0].score if cands else 0.0
        second = cands[1].score if len(cands) > 1 else 0.0
        if top < self.low:
            return Route(None, "none", cands, seconds=time.monotonic() - t0)
        decoy, d_score = self.index.near_miss(text)
        if decoy and d_score >= self.low and d_score >= top - NEAR_MARGIN:
            return Route(None, "near-miss", cands, model_raw=decoy, seconds=time.monotonic() - t0)
        if (top >= self.high and top - second >= self.margin) or (top >= self.accept and top - second >= LEAD):
            return Route(cands[0].branch.id, "index", cands, seconds=time.monotonic() - t0)
        if self.model is None:  # index-only mode: trust the leader only above ACCEPT
            if top < self.accept:
                return Route(None, "none", cands, seconds=time.monotonic() - t0)
            return Route(cands[0].branch.id, "index-only", cands, seconds=time.monotonic() - t0)
        options = [(c.branch.id, f"{c.branch.title} — {c.branch.description}") for c in cands[:self.k]]
        mc = self.model.choose(text, options, context)
        if mc.choice is None:
            return Route(None, "model-failed", cands, mc.seconds, mc.raw, time.monotonic() - t0)
        if mc.choice == "ask" and top >= self.accept:
            # The model is unsure but the index alone would have trusted its leader: use it.
            return Route(cands[0].branch.id, "index-after-ask", cands, mc.seconds, mc.raw, time.monotonic() - t0)
        if mc.choice in ("none", "ask"):
            return Route(None, mc.choice, cands, mc.seconds, mc.raw, time.monotonic() - t0)
        return Route(mc.choice, "model", cands, mc.seconds, mc.raw, time.monotonic() - t0)
