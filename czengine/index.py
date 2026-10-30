"""Type-ahead index: every word narrows the branch set. Deterministic, no model.

Each branch's patterns are known phrasings. A query is scored against every pattern with an
IDF-weighted overlap (rare, meaningful words count; filler words barely count), synonyms are
folded to one canonical word, and bigrams add a bonus for word order. The best pattern score
per branch is that branch's score in [0, 1], plus a small bonus for how much of the query the
branch's whole vocabulary explains (breaks ties between branches that each match one short pattern).

A branch with `core:` groups can only be a candidate if every group has a word in the query.
Near-miss phrasings (`near-misses.yaml`: out-of-scope look-alikes such as a woodworking router)
are scored the same way; `near_miss()` returns the best one so the router can refuse.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from czengine.library import Branch, Pack
from czengine.normalize import STOP

WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")
FILLER = (STOP - {"no", "not", "down", "up", "off", "new", "old", "why", "how", "what", "where", "who", "when"}) | {
    # generic verbs and fillers that say nothing about the topic ("how do I set up X", "can you help me")
    "set", "setup", "make", "get", "use", "using", "help", "know", "show", "tell", "give", "try", "trying",
    "should", "need", "want", "really", "please", "thing", "things", "something", "anything", "stuff",
    "our", "us", "we", "im", "i'm", "ive", "i've", "keeps", "keep", "again", "every", "always", "just", "even"}


def words(text: str) -> list[str]:
    out = []
    for w in WORD.findall(text.lower().replace("\u2019", "'")):
        w = w.strip("'-")
        if w.endswith("'s"):  # possessive: "phone's wifi" -> phone wifi
            w = w[:-2]
        if w:
            out.append(w)
    return out


@dataclass
class Candidate:
    branch: Branch
    score: float
    matched: list[str]


COVER_BONUS = 0.15   # weight of the branch-vocabulary coverage tie-breaker (tuned on dev + dev2)


def core_hit(word: str, qset: set[str]) -> bool:
    return word in qset or any(t == word + "s" or (len(word) >= 4 and t.startswith(word)) for t in qset)


class Index:
    def __init__(self, packs: list[Pack]):
        self.branches = [b for p in packs for b in p.branches]
        self.canon: dict[str, str] = {}
        self.conflicts: dict[str, set[str]] = {}   # variant -> heads it has in different packs (not folded)
        for p in packs:
            for head, variants in p.synonyms.items():
                for v in [head, *variants]:
                    ws = words(v)
                    if not ws:
                        continue
                    key = ws[0] if len(ws) == 1 else "_".join(ws)  # multi-word synonym ("wi fi") -> head word
                    if key in self.conflicts:
                        self.conflicts[key].add(head)
                    elif key in self.canon and self.canon[key] != head:
                        # "freezing" is lag in one pack and hypothermia in another: keep the plain word
                        self.conflicts[key] = {self.canon.pop(key), head}
                    else:
                        self.canon[key] = head
        self.other_devices = {p.name: (p.other_devices, p.this_device) for p in packs if p.other_devices}
        self.multi = sorted((k for k in self.canon if "_" in k), key=lambda k: -k.count("_"))
        self.patterns: list[tuple[Branch, list[str]]] = []
        for b in self.branches:
            for text in [*b.patterns, b.title]:
                self.patterns.append((b, self.terms(text)))
            if b.keywords:
                self.patterns.append((b, self.terms(" ".join(b.keywords))))
        self.core = {b.id: [[w if w in self.conflicts else self.canon.get(w, w) for w in g] for g in b.core]
                     for b in self.branches if b.core}
        self.decoys: list[tuple[str, list[str]]] = [(f"none.{topic}", self.terms(t)) for p in packs
                                                    for topic, pats in p.near_misses.items() for t in pats]
        self.decoys = [(i, toks) for i, toks in self.decoys if toks]
        self.vocab_of: dict[str, set[str]] = {}
        df: dict[str, set[str]] = {}
        for b, toks in self.patterns:
            self.vocab_of.setdefault(b.id, set()).update(toks)
            for t in toks:
                df.setdefault(t, set()).add(b.id)
        for did, toks in self.decoys:
            for t in toks:
                df.setdefault(t, set()).add(did)
        n = max(len(self.branches), 1)
        self.idf = {t: math.log(1 + n / len(ids)) for t, ids in df.items()}
        self.stem_of: dict[str, str] = {}
        for t in sorted(self.idf, key=len):  # shortest library word wins a shared key
            self.stem_of.setdefault(self.stem_key(t), t)
        # A query word the library has never seen is strong evidence the question is about
        # something else, so it weighs as much as the rarest library word (pushes toward "none").
        self.unknown_weight = math.log(1 + n)
        self.vocabulary = set(df) | {w for b in self.branches for p in b.patterns for w in words(p)}

    STATE_KEYS = ("canon", "multi", "core", "decoys", "idf", "stem_of", "unknown_weight", "other_devices")

    def to_state(self) -> dict:
        """Everything derived from the packs, as plain JSON (see czengine/compiled.py)."""
        st = {k: getattr(self, k) for k in self.STATE_KEYS}
        st["patterns"] = [[b.id, toks] for b, toks in self.patterns]
        st["vocab_of"] = {k: sorted(v) for k, v in self.vocab_of.items()}
        st["vocabulary"] = sorted(self.vocabulary)
        st["conflicts"] = {k: sorted(v) for k, v in self.conflicts.items()}
        return st

    @classmethod
    def from_state(cls, packs: list[Pack], st: dict) -> "Index":
        self = cls.__new__(cls)
        self.branches = [b for p in packs for b in p.branches]
        by_id = {b.id: b for b in self.branches}
        for k in cls.STATE_KEYS:
            setattr(self, k, st[k])
        self.decoys = [(i, toks) for i, toks in st["decoys"]]
        self.patterns = [(by_id[i], toks) for i, toks in st["patterns"]]
        self.vocab_of = {k: set(v) for k, v in st["vocab_of"].items()}
        self.vocabulary = set(st["vocabulary"])
        self.conflicts = {k: set(v) for k, v in st["conflicts"].items()}
        return self

    def terms(self, text: str, expand: bool = True) -> list[str]:
        """Canonical non-filler words. expand=False keeps cross-pack ambiguous words literal (for core gates)."""
        ws = words(text)
        joined = " ".join(ws)
        for m in self.multi:  # fold multi-word synonyms first ("wi fi" -> wifi)
            phrase = m.replace("_", " ")
            if phrase in joined:
                joined = re.sub(rf"\b{re.escape(phrase)}\b", self.canon[m], joined)
        out = []
        for w in joined.split():
            if w in self.conflicts:  # ambiguous across packs: keep every meaning, other words decide
                out += sorted(self.conflicts[w]) if expand else [w]
                continue
            w = self.canon.get(w, w)
            if w not in FILLER:
                out.append(w)
        return out

    def weight(self, t: str) -> float:
        return self.idf.get(t, 0.0)

    @staticmethod
    def stem_key(w: str) -> str:
        """Crude shared key for inflections: flushing/flushed/flushes -> flush, hogging -> hog, burns -> burn."""
        if len(w) <= 3 or not w.isalpha():
            return w
        for suf in ("ing", "ed", "es", "s", "er"):
            if w.endswith(suf) and not w.endswith("ss") and len(w) - len(suf) >= 3:
                w = w[: -len(suf)]
                if len(w) >= 3 and w[-1] == w[-2] and w[-1] not in "ls":
                    w = w[:-1]
                break
        return w[:-1] if len(w) > 3 and w.endswith("e") else w

    def stem(self, w: str) -> str:
        """Map an unknown inflection onto the library word with the same stem key."""
        return w if w in self.idf else self.stem_of.get(self.stem_key(w), w)

    def _query(self, text: str, prefix: bool) -> list[str]:
        q = [self.stem(t) for t in self.terms(text)]
        if prefix and q and q[-1] not in self.idf:  # type-ahead: complete the last partial word
            part = q[-1]
            completions = [t for t in self.idf if t.startswith(part)] if len(part) >= 2 else []
            q = q[:-1] + (completions[:8] or [part])
        return q

    def _score(self, q: list[str], qset: set[str], qbig: set, q_all: float, toks: list[str]) -> tuple[float, set]:
        tset = set(toks)
        common = qset & tset
        if not common:
            return 0.0, common
        m_w = sum(self.weight(t) for t in common)
        p_w = sum(self.weight(t) for t in tset) or 1e-9
        # Long, rambling messages carry many unrelated words; count the query side at most 1.5x the
        # pattern's weight so length alone doesn't sink a genuine match (tuned on dev + dev2).
        q_w = min(q_all, 1.5 * p_w)
        score = m_w / (p_w * q_w) ** 0.5
        big = qbig & set(zip(toks, toks[1:]))
        return min(1.0, score + 0.05 * len(big)), common

    def eligible(self, branch_id: str, qset: set[str]) -> bool:
        groups = self.core.get(branch_id, ())
        if not groups:
            return True
        keys = {self.stem_key(t) for t in qset}
        return all(any(core_hit(w, qset) or self.stem_key(w) in keys for w in g) for g in groups)

    def search(self, text: str, k: int = 5, prefix: bool = False) -> list[Candidate]:
        q = self._query(text, prefix)
        qset = set(q)
        qcore = {self.stem(t) for t in self.terms(text, expand=False)} if self.conflicts else qset
        qbig = set(zip(q, q[1:]))
        q_all = sum(self.idf.get(t, self.unknown_weight) for t in qset) or 1e-9
        best: dict[str, Candidate] = {}
        for b, toks in self.patterns:
            if not prefix and not self.eligible(b.id, qcore):
                continue
            score, common = self._score(q, qset, qbig, q_all, toks)
            if not common:
                continue
            cur = best.get(b.id)
            if cur is None or score > cur.score:
                best[b.id] = Candidate(b, score, sorted(common))
        for c in best.values():  # tie-break: share of the query the branch's whole vocabulary explains
            cover = sum(self.weight(t) for t in qset & self.vocab_of[c.branch.id]) / q_all
            c.score = round(c.score + COVER_BONUS * cover * (1.0 - c.score), 4)  # never closes a gap at the top
        return sorted(best.values(), key=lambda c: -c.score)[:k]

    def other_device(self, text: str, branch) -> str | None:
        """The other device a message is about, if the branch can only act on this computer or the router."""
        if branch.scope not in ("computer", "router") or branch.pack not in self.other_devices:
            return None
        others, mine = self.other_devices[branch.pack]
        joined = " " + " ".join(words(text)) + " "
        if any(f" {m} " in joined for m in mine):
            return None
        return next((o for o in others if f" {o} " in joined or f" {o}s " in joined), None)

    def near_miss(self, text: str) -> tuple[str | None, float]:
        """Best-matching out-of-scope look-alike (topic id, score), scored like a branch pattern."""
        q = self._query(text, False)
        qset = set(q)
        qbig = set(zip(q, q[1:]))
        q_all = sum(self.idf.get(t, self.unknown_weight) for t in qset) or 1e-9
        top: tuple[str | None, float] = (None, 0.0)
        for did, toks in self.decoys:
            score, _ = self._score(q, qset, qbig, q_all, toks)
            if score > top[1]:
                top = (did, round(score, 4))
        return top
