"""Download a published fine-tuned router (GitHub Release), verify its SHA-256, register it with Ollama.

  cz model get lfm      LFM2.5-1.2B router (LFM Open License v1.0) — default
  cz model get llama    Llama 3.2 1B router (Llama 3.2 Community License; "Built with Llama")
  cz model list

Files land in ~/.local/share/cortexzero/models/<release>/. The licence files are downloaded next to the model.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import urllib.request
from pathlib import Path

RELEASE = "v0.1.0"
BASE = f"https://github.com/TheRealBrofessor/cortexzero-engine/releases/download/{RELEASE}/"
DEST = Path.home() / ".local/share/cortexzero/models" / RELEASE

ROUTERS = {
    "lfm": {"ollama": "cz-router-lfm2.5", "gguf": "cz-router-lfm2.5-1.2b-Q4_K_M.gguf",
            "sha256": "6261c0d558113c56d50f12730c0e9f6810414389f08ec2dae5b783405aa6f581",
            "modelfile": "Modelfile.lfm", "licences": ["LICENSE-LFM-Open-License-v1.0.txt", "NOTICE.txt"],
            "note": "LFM Open License v1.0: free incl. commercial use, except entities with >= USD 10M annual revenue."},
    "llama": {"ollama": "llama-3.2-1b-cz-router", "gguf": "Llama-3.2-1B-cz-router-Q4_K_M.gguf",
              "sha256": "87561fdf3f6e34c3bf12b72471dc0d5eebdd986523e754c62e2dc011ff86650f",
              "modelfile": "Modelfile.llama",
              "licences": ["LICENSE-Llama-3.2-Community.txt", "Llama-3.2-Acceptable-Use-Policy.md", "NOTICE.txt"],
              "note": "Built with Llama. Llama 3.2 Community License + Acceptable Use Policy apply."},
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(name: str, dest: Path, fetch=None) -> Path:
    out = dest / name
    if out.exists():
        return out
    tmp = out.with_suffix(out.suffix + ".part")
    print(f"  downloading {name} …", flush=True)
    if fetch:
        fetch(BASE + name, tmp)
    else:
        with urllib.request.urlopen(BASE + name, timeout=60) as r, tmp.open("wb") as fh:
            shutil.copyfileobj(r, fh, 1 << 20)
    tmp.rename(out)
    return out


def get(which: str, fetch=None, register: bool = True) -> str:
    if which not in ROUTERS:
        raise SystemExit(f"unknown router {which!r}; choose one of: {', '.join(ROUTERS)}")
    spec = ROUTERS[which]
    DEST.mkdir(parents=True, exist_ok=True)
    for lic in spec["licences"]:
        _download(lic, DEST, fetch)
    _download(spec["modelfile"], DEST, fetch)
    gguf = _download(spec["gguf"], DEST, fetch)
    got = sha256(gguf)
    if got != spec["sha256"]:
        gguf.unlink()
        raise SystemExit(f"checksum mismatch for {spec['gguf']} (got {got}); file removed, try again")
    print(f"  checksum OK. {spec['note']} Licence files: {DEST}")
    if register:
        if not shutil.which("ollama"):
            raise SystemExit(f"Ollama not found. Install it from https://ollama.com, then run:\n"
                             f"  cd {DEST} && ollama create {spec['ollama']} -f {spec['modelfile']}")
        subprocess.run(["ollama", "create", spec["ollama"], "-f", spec["modelfile"]], cwd=DEST, check=True,
                       stdin=subprocess.DEVNULL)
    print(f"ready: cz ask --model {spec['ollama']} \"…\"")
    return spec["ollama"]


def listing() -> str:
    return "\n".join(f"{k:6} → ollama name {v['ollama']:24} {v['gguf']}  ({v['note']})" for k, v in ROUTERS.items())
