#!/usr/bin/env python3
"""Turn Hermes-written messages + two blind label files into a held-out case file.

Only messages where both labellers chose the same label set are kept; disagreements are written to
a separate review file for the owner. Labels must be known branch ids or "none".

  build_heldout_from_hermes.py MESSAGES LABELS_A LABELS_B OUT_CASES OUT_DISPUTES [VERSION] [PACK_DIR...]
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from czengine.library import load_packs  # noqa: E402

msgs, la, lb, out, disp = map(Path, sys.argv[1:6])
version = sys.argv[6] if len(sys.argv) > 6 else "v2"
pack_dirs = [Path(p) for p in sys.argv[7:]] or [ROOT / "packs/home-network"]
ids = {b.id for p in load_packs(pack_dirs) for b in p.branches} | {"none"}
M = {m["id"]: m["text"] for m in yaml.safe_load(msgs.read_text())}


def labels(p):
    out = {}
    for x in yaml.safe_load(p.read_text()):
        e = x.get("expect") or []
        e = [e] if isinstance(e, str) else e
        out[x["id"]] = sorted(set(e)) if e and all(i in ids for i in e) and not ("none" in e and len(e) > 1) else None
    return out


A, B = labels(la), labels(lb)
cases, disputes, n_none = [], [], 0
for mid, text in M.items():
    a, b = A.get(mid), B.get(mid)
    if a and b and a == b:
        cases.append({"id": mid, "report": text, "expect": a})
        n_none += a == ["none"]
    else:
        disputes.append({"id": mid, "report": text, "labeller_a": a, "labeller_b": b})
head = (f"# HELD-OUT {version}: written by Hermes czcasewriter, blind-labelled by czlabela + czlabelb; only agreed labels.\n"
        f"# {len(cases)}/{len(M)} agreed ({n_none} none). Never tune on this file.\n")
out.write_text(head + yaml.safe_dump(cases, sort_keys=False, allow_unicode=True, width=110))
disp.write_text("# Labeller disagreements: owner review, not used for scoring.\n" +
                yaml.safe_dump(disputes, sort_keys=False, allow_unicode=True, width=110))
print(f"agreed {len(cases)}/{len(M)} (none {n_none}); disputes {len(disputes)}")
