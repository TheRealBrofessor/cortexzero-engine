"""Optional online answer sources. OFF unless the user switches them on (`--online`).

A branch may name one https URL (`online:`). When online answers are on, the page is fetched once,
reduced to plain text, cached on disk with its fetch time, and shown AFTER the offline answer with
the URL and date, so the reader can always tell library text from fetched text. Failures fall
back silently to the offline answer (which is always complete on its own).
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.request
from pathlib import Path

TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)


def plain_text(raw: str, limit: int = 600) -> str:
    text = html.unescape(TAGS.sub(" ", raw))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


class OnlineSource:
    def __init__(self, cache_dir: Path, ttl_s: float = 7 * 86400, timeout: float = 5.0, fetch=None):
        self.cache_dir, self.ttl_s, self.timeout = cache_dir, ttl_s, timeout
        self._fetch = fetch or self._http

    def _http(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "cortexzero/0.1 (+offline-first help engine)"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read(200_000).decode("utf-8", "replace")

    def get(self, url: str) -> tuple[str, float] | None:
        """(excerpt, fetched_at) from cache or network; None on any failure."""
        if not url.startswith("https://"):
            return None
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")
        try:
            hit = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - hit["fetched_at"] < self.ttl_s:
                return hit["text"], hit["fetched_at"]
        except (OSError, ValueError, KeyError):
            pass
        try:
            text = plain_text(self._fetch(url))
        except Exception:  # network, TLS, decode: the offline answer stands alone
            return None
        if not text:
            return None
        now = time.time()
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "fetched_at": now, "text": text}), encoding="utf-8")
        except OSError:
            pass
        return text, now
