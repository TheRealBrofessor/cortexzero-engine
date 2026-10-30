"""Input cleanup: protect exact spans, fix typos against a vocabulary, never change meaning.

Protected spans (paths, URLs, IPs, commands in backticks, IDs) are kept verbatim and never
"corrected". Typo fixing only replaces a word that is unknown AND within a small edit
distance of exactly one clearly-better vocabulary word. The original text is always kept.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PROTECT = re.compile(
    r"`[^`]+`"                                  # inline code
    r"|https?://\S+"                            # URLs
    r"|(?:~|/)[\w./-]+"                         # paths
    r"|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"    # IPv4 (+port)
    r"|\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b"      # MAC
    r"|\b\w*\d\w*_\w+\b"                        # ids like 20260916_201209_63dc9e
)
WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")

# Common English words that must never be "corrected" into something else.
STOP = set("""a an the i me my we our you your it its is are was were be been am do does did
done to of in on at for from with by as and or but not no yes ok okay so if then than that this
these those there here what whats what's who why when where which how can could should would will
just get got have has had like want need make let lets please help thanks thank still keep go up
down out off over again now new old all any some more most very too also into about after before
work works working doesnt don't dont won't wont can't cant isnt aren't""".split())


def edit_distance(a: str, b: str, limit: int = 2) -> int:
    """Damerau-Levenshtein (adjacent transposition), early exit above limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


@dataclass
class Normalized:
    original: str
    text: str                      # cleaned, lower-cased for matching
    tokens: list[str]
    protected: list[str] = field(default_factory=list)
    corrections: dict[str, str] = field(default_factory=dict)


def system_dictionary(path: str = "/usr/share/dict/words") -> set[str]:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return {w.strip().lower() for w in fh if w.strip().isalpha()}
    except OSError:
        return set()


class Normalizer:
    """Known words = English dictionary + pack vocabulary (+ personal words). Only unknown words are
    corrected, and preferably toward pack vocabulary (the words the library can actually route on)."""

    def __init__(self, vocabulary: set[str], dictionary: set[str] | None = None, personal: set[str] | None = None):
        self.domain = {w.lower() for w in vocabulary} | STOP
        self.known = self.domain | (system_dictionary() if dictionary is None else dictionary) | (personal or set())
        self._by_len: dict[int, list[str]] = {}
        for w in self.domain:
            self._by_len.setdefault(len(w), []).append(w)
        self._dict_by_len: dict[int, list[str]] = {}
        for w in self.known - self.domain:
            self._dict_by_len.setdefault(len(w), []).append(w)

    def _nearest(self, w: str, table: dict[int, list[str]], limit: int) -> list[tuple[int, str]]:
        best = []
        for n in range(len(w) - limit, len(w) + limit + 1):
            for cand in table.get(n, ()):
                d = edit_distance(w, cand, limit)
                if d <= limit:
                    best.append((d, cand))
        return sorted(best)

    def correct(self, word: str) -> str | None:
        w = word.lower()
        if w in self.known or len(w) < 4 or not w.isalpha():
            return None
        limit = 1 if len(w) < 7 else 2
        for table in (self._by_len, self._dict_by_len):  # domain words first, then general English
            best = self._nearest(w, table, limit)
            if best:
                # Only correct when the closest candidate is unique (never guess between ties).
                if len(best) > 1 and best[0][0] == best[1][0]:
                    return None
                return best[0][1]
        return None

    def __call__(self, text: str) -> Normalized:
        protected = [m.group(0) for m in PROTECT.finditer(text)]
        masked = PROTECT.sub(" ", text)
        corrections: dict[str, str] = {}
        tokens: list[str] = []
        for m in WORD.finditer(masked):
            raw = m.group(0).lower().strip("'-")
            if not raw:
                continue
            fixed = self.correct(raw)
            if fixed and fixed != raw:
                corrections[raw] = fixed
                raw = fixed
            tokens.append(raw)
        return Normalized(original=text, text=" ".join(tokens), tokens=tokens,
                          protected=protected, corrections=corrections)
