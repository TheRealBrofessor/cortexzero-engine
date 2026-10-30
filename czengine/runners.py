"""Runner executes ONE program with fixed argv (never a shell) and returns raw output.

LocalRunner : subprocess, no shell, stdin closed, minimal environment.
`mode` is "check" (read-only branch) or "action" (consented change); it is recorded for evidence.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from czengine.policy import allowed

SAFE_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


@dataclass
class RawResult:
    argv: list[str]
    rc: int
    out: str
    seconds: float


class LocalRunner:
    name = "local"

    def run(self, argv: list[str], timeout: float, mode: str) -> RawResult:
        t0 = time.monotonic()
        why = allowed(argv, mode)
        if why:  # second line of defence: the pack loader already refused this
            return RawResult(argv, 126, f"blocked by policy: {why}", 0.0)
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL, env=SAFE_ENV)
            rc, out = p.returncode, p.stdout + p.stderr
        except FileNotFoundError:
            rc, out = 127, f"{argv[0]}: not installed"
        except subprocess.TimeoutExpired:
            rc, out = 124, "timed out"
        return RawResult(argv, rc, out.strip()[:8000], round(time.monotonic() - t0, 3))
