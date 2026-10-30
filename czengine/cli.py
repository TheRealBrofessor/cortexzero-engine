"""cz — CortexZero engine command line.

  cz ask "my wifi says connected but nothing loads"     one question
  cz repl                                                conversation with live type-ahead (Tab takes a hint)
  cz suggest "how do i fi"                               type-ahead suggestions only
  cz compile                                             precompile packs for fast startup
  cz pack list | validate [DIR...] | new NAME | tree     pack tools
  cz evidence --last 10                                  what the engine did, from the evidence log
Options: --model <ollama tag> (omit = index only, no model) · --online (fetch cited online sources; off by default)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from czengine.engine import Engine
from czengine.runners import LocalRunner

ROOT = Path(__file__).resolve().parents[1]
DATA = Path.home() / ".local/share/cortexzero"
CACHE = Path.home() / ".cache/cortexzero"
COMPILED = CACHE / "packs.compiled.json"


def pack_dirs(args) -> list[Path]:
    if args.pack:
        return [Path(p).resolve() for p in args.pack]
    return sorted(p.resolve() for p in (ROOT / "packs").iterdir() if (p / "pack.yaml").exists())


def build(args) -> Engine:
    from czengine.model import OllamaRouterModel
    from czengine.online import OnlineSource
    model = OllamaRouterModel(args.model, args.url) if args.model else None
    online = OnlineSource(CACHE / "online") if args.online else None
    return Engine.from_dirs(pack_dirs(args), compiled=None if args.no_compiled else Path(args.compiled),
                            model=model, runner=LocalRunner(), online=online,
                            evidence=Path(args.evidence) if args.evidence else None)


def show(turn) -> None:
    print(f"[{turn.label}] {turn.answer}")
    if turn.corrections:
        print(f"   (read as: {', '.join(f'{a}→{b}' for a, b in turn.corrections.items())})")


def cmd_pack(a) -> int:
    from czengine import packtools
    sub = a.text[0] if a.text else "list"
    rest = a.text[1:]
    if sub == "new":
        if not rest:
            print("usage: cz pack new NAME [--domain D]", file=sys.stderr)
            return 2
        root = packtools.new_pack(rest[0], Path(a.into), domain=a.domain or "")
        print(f"created {root} (pack.yaml, branches/example.yaml, near-misses.yaml, cases/dev.yaml)")
        return 0
    dirs = [Path(p).resolve() for p in rest] if rest else pack_dirs(a)
    if sub == "list":
        for d in packtools.describe(dirs):
            if "error" in d:
                print(f"✗ {d['name']:16} BROKEN: {d['error']}")
            else:
                crit = " SAFETY-CRITICAL" if d["safety_critical"] else ""
                print(f"• {d['name']:16} v{d['version']:7} {d['status']:10} {d['branches']:3} branches  "
                      f"{d['near_miss_topics']:2} near-miss topics  {d['safety_rules']} safety rules{crit}  {d['path']}")
        return 0
    if sub == "validate":
        errors, warns = packtools.lint(dirs)
        for e in errors:
            print("ERROR", e)
        for w in warns:
            print("warn ", w)
        print(f"{len(dirs)} pack(s): {len(errors)} error(s), {len(warns)} warning(s)")
        return 1 if errors else 0
    if sub == "tree":
        from czengine.library import load_packs
        print(packtools.tree(load_packs(dirs)))
        return 0
    print(f"unknown pack command {sub!r} (list | validate | new | tree)", file=sys.stderr)
    return 2


def cmd_evidence(a) -> int:
    path = Path(a.evidence)
    if not path.exists():
        print(f"no evidence log yet at {path}")
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()[-a.last:]
    for ln in lines:
        try:
            t = json.loads(ln)
        except ValueError:
            continue
        if a.json:
            print(json.dumps(t, ensure_ascii=False))
            continue
        r = t.get("route") or {}
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t.get("ts", 0)))
        route = r.get("branch_id") or r.get("rule") or "none"
        print(f"{when}  {t.get('label', '')}  [{r.get('decided_by', '?')} → {route}]  {t.get('seconds', 0):.2f}s")
        print(f"   Q: {t.get('question', '')}")
        out = t.get("outcome") or {}
        for s in out.get("steps", []):
            print(f"   $ {' '.join(s['argv'])}  (rc {s['rc']}, {s['seconds']}s)")
        first = (t.get("answer") or "").splitlines()
        print(f"   A: {first[0] if first else ''}{' …' if len(first) > 1 else ''}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cz", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["ask", "repl", "suggest", "compile", "pack", "evidence"])
    ap.add_argument("text", nargs="*")
    ap.add_argument("--model")
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--pack", action="append", help="pack directory (repeatable); default: all in packs/")
    ap.add_argument("--evidence", default=str(DATA / "evidence.jsonl"))
    ap.add_argument("--compiled", default=str(COMPILED))
    ap.add_argument("--no-compiled", action="store_true", help="always load the YAML packs")
    ap.add_argument("--online", action="store_true", help="show cited online sources for answers (cached)")
    ap.add_argument("--last", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--into", default=str(ROOT / "packs"), help="pack new: parent directory")
    ap.add_argument("--domain")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "pack":
        return cmd_pack(a)
    if a.cmd == "evidence":
        return cmd_evidence(a)
    if a.cmd == "compile":
        from czengine.compiled import compile_packs
        s = compile_packs(pack_dirs(a), Path(a.compiled))
        print(f"compiled {s['packs']} packs, {s['branches']} branches, {s['terms']} terms → {a.compiled} ({s['bytes']} bytes)")
        return 0
    eng = build(a)
    if a.debug:
        print(f"(packs from {'compiled cache' if eng.compiled else 'YAML'})", file=sys.stderr)
    if a.cmd == "suggest":
        for s in eng.suggest(" ".join(a.text)):
            print("•", s)
        return 0
    if a.cmd == "ask":
        t = eng.ask(" ".join(a.text))
        show(t)
        if a.debug:
            print(t.route)
        return 0
    from czengine.tui import read_line
    print("CortexZero — ask anything in the installed packs. Hints appear as you type (Tab takes one). 'quit' exits.")
    while True:
        line = read_line("you> ", eng.suggest)
        if line is None:
            return 0
        line = line.strip()
        if line in ("quit", "exit"):
            return 0
        if line.startswith("?"):          # old-style explicit suggestion request
            for s in eng.suggest(line[1:]):
                print("  •", s)
            continue
        if line:
            t = eng.ask(line)
            show(t)
            if a.debug:
                print("  ", t.route)


if __name__ == "__main__":
    sys.exit(main())
