"""Branches and packs: load, validate, and expose them to the index and handlers.

A branch is data, never model output. Its executable steps are fixed strings written by a
person (or drafted by an agent and then gated). The router only ever returns a branch id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from czengine.policy import allowed

ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)+$")
KINDS = {"answer", "check", "action"}
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
SLOT_TYPES = {"host": re.compile(rf"^(?:[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?\.)+[a-z]{{2,63}}$|^{_OCTET}(?:\.{_OCTET}){{3}}$", re.I),
              "port": re.compile(r"^\d{1,5}$")}
# A value carried from one step's output into a later step's argv must look like an address or name.
CHAIN_VALUE = re.compile(r"^[A-Za-z0-9_.:%][A-Za-z0-9_.:%-]{0,252}$")   # never starts with "-" (no option injection)
PROGRAM = re.compile(r"^[a-z0-9][a-z0-9._+-]*$")
TRANSFORMS = {"mbps_from_bytes_per_s"}
STATUSES = ("draft", "unverified", "reviewed")
SCOPES = ("computer", "router", "network", "any")   # reviewed = a named person checked every fact
HTTPS_URL = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?(?:/\S*)?$")


@dataclass
class Step:
    """One program run with fixed arguments (no shell). Output post-processing is done in Python:
    `extract` regex (multiline) → each match formatted with `fmt` ("{1}" = group 1), optional
    `first_only`, `max_lines`, and a named `transform`. {name} in argv = slot or earlier step value."""
    id: str
    argv: list[str]
    timeout: float = 10.0
    extract: str | None = None
    fmt: str | None = None
    first_only: bool = False
    max_lines: int = 40
    transform: str | None = None
    allow_fail: bool = False     # action steps: keep going if this one fails (default: stop at the first failure)


@dataclass
class Rule:
    say: str
    step: str | None = None
    rc_nonzero: bool | None = None
    contains: str | None = None
    missing: str | None = None
    empty: bool | None = None
    matches: str | None = None   # regex (case-insensitive) that must match the step output
    failed: bool = False         # when this rule fires, the outcome is a failure (label ⚠), not a success


@dataclass
class Branch:
    id: str
    title: str
    description: str
    patterns: list[str]
    kind: str
    pack: str
    keywords: list[str] = field(default_factory=list)
    text: str = ""
    steps: list[Step] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    default: str = ""
    slots: dict[str, str] = field(default_factory=dict)
    risk: int = 0
    verify: Step | None = None
    sources: list[str] = field(default_factory=list)
    core: list[list[str]] = field(default_factory=list)   # AND of OR-groups; the branch can win only if each group matches
    scope: str = "any"   # computer | router | network | any: what the answer acts on (see Pack.other_devices)
    online: str = ""     # optional https source shown (cached, cited) only when online answers are switched on


@dataclass
class SafetyRule:
    id: str
    pattern: re.Pattern
    response: str


@dataclass
class Pack:
    name: str
    version: str
    branches: list[Branch]
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    safety: list[SafetyRule] = field(default_factory=list)
    near_misses: dict[str, list[str]] = field(default_factory=dict)  # topic -> out-of-scope look-alike phrasings
    description: str = ""
    domain: str = ""
    status: str = "draft"
    safety_critical: bool = False
    sources: list[str] = field(default_factory=list)
    reviewed_by: str = ""
    root: str = ""
    other_devices: list[str] = field(default_factory=list)   # devices this pack can't check or change
    this_device: list[str] = field(default_factory=list)     # words meaning "this computer"


class PackError(ValueError):
    pass


def _check_fmt(fmt, extract: str | None, where: str, sid: str) -> None:
    import string
    if fmt is None:
        return
    if not isinstance(fmt, str):
        raise PackError(f"{where}: step {sid}: fmt must be a string")
    groups = re.compile(extract, re.M).groups if extract else 0
    try:
        fields = [f for _, f, _, _ in string.Formatter().parse(fmt) if f is not None]
    except ValueError as e:
        raise PackError(f"{where}: step {sid}: bad fmt: {e}") from None
    for f in fields:
        if not f.isdigit() or not 1 <= int(f) <= groups:
            raise PackError(f"{where}: step {sid}: fmt field {{{f}}} must be a group number 1..{groups}")


def _step(d: dict, where: str, mode: str = "check") -> Step:
    if not isinstance(d, dict) or not isinstance(d.get("id"), str):
        raise PackError(f"{where}: step needs a string id")
    if "run" in d:
        raise PackError(f"{where}: step {d['id']}: shell strings are not allowed; use argv")
    argv = d.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise PackError(f"{where}: step {d['id']} needs argv: a non-empty list of strings")
    if not PROGRAM.match(argv[0]):
        raise PackError(f"{where}: step {d['id']}: argv[0] must be a bare program name, got {argv[0]!r}")
    if d.get("transform") and d["transform"] not in TRANSFORMS:
        raise PackError(f"{where}: step {d['id']}: unknown transform {d['transform']!r}")
    if d.get("extract"):
        try:
            re.compile(d["extract"], re.M)
        except re.error as e:
            raise PackError(f"{where}: step {d['id']}: bad extract regex: {e}") from None
    _check_fmt(d.get("fmt"), d.get("extract"), where, d["id"])
    why = allowed(list(argv), mode)
    if why:
        raise PackError(f"{where}: step {d['id']}: {why}")
    return Step(d["id"], list(argv), float(d.get("timeout", 10)), d.get("extract"), d.get("fmt"),
                bool(d.get("first_only", False)), int(d.get("max_lines", 40)), d.get("transform"),
                bool(d.get("allow_fail", False)))


def _core(raw, where: str) -> list[list[str]]:
    """`core: [a, b]` = one group (a OR b); `core: [[a, b], [c]]` = (a OR b) AND c."""
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        raise PackError(f"{where}: core must be a non-empty list")
    groups = raw if all(isinstance(g, list) for g in raw) else [raw]
    for g in groups:
        if not g or not all(isinstance(w, str) and w.strip() for w in g):
            raise PackError(f"{where}: core groups must be non-empty lists of words")
    return [[w.strip().lower() for w in g] for g in groups]


def load_branch(path: Path, pack: str) -> Branch:
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    where = f"{pack}/{path.name}"
    for key in ("id", "title", "description", "kind"):
        if not isinstance(d.get(key), str) or not d[key].strip():
            raise PackError(f"{where}: missing {key}")
    if not ID_RE.match(d["id"]):
        raise PackError(f"{where}: bad id {d['id']!r}")
    if d["kind"] not in KINDS:
        raise PackError(f"{where}: kind must be one of {sorted(KINDS)}")
    pats = d.get("patterns")
    if not isinstance(pats, list) or len(pats) < 2 or not all(isinstance(p, str) for p in pats):
        raise PackError(f"{where}: needs at least 2 string patterns")
    b = Branch(id=d["id"], title=d["title"], description=d["description"], patterns=pats,
               kind=d["kind"], pack=pack, keywords=list(d.get("keywords", [])),
               text=d.get("text", ""), default=d.get("default", ""),
               slots=dict(d.get("slots", {})), risk=int(d.get("risk", 0)),
               sources=list(d.get("sources", [])), core=_core(d.get("core"), where),
               online=str(d.get("online", "") or ""), scope=str(d.get("scope", "any")))
    if b.scope not in SCOPES:
        raise PackError(f"{where}: scope must be one of {SCOPES}")
    if b.online and not HTTPS_URL.match(b.online):
        raise PackError(f"{where}: online must be an https:// URL")
    for s, t in b.slots.items():
        if t not in SLOT_TYPES:
            raise PackError(f"{where}: slot {s} has unknown type {t}")
    if b.kind == "answer" and not b.text.strip():
        raise PackError(f"{where}: answer branch needs text")
    if b.kind in ("check", "action"):
        b.steps = [_step(s, where, b.kind) for s in d.get("steps", [])]
        if not b.steps:
            raise PackError(f"{where}: {b.kind} branch needs steps")
        ids = {s.id for s in b.steps}
        for r in d.get("rules", []):
            rule = Rule(say=r["say"], step=r.get("step"), rc_nonzero=r.get("rc_nonzero"),
                        contains=r.get("contains"), missing=r.get("missing"), empty=r.get("empty"),
                        matches=r.get("matches"), failed=bool(r.get("failed", False)))
            if rule.matches is not None:
                try:
                    re.compile(rule.matches)
                except re.error as e:
                    raise PackError(f"{where}: rule matches: bad regex: {e}") from None
            if rule.step and rule.step not in ids:
                raise PackError(f"{where}: rule refers to unknown step {rule.step}")
            b.rules.append(rule)
        if not b.default:
            raise PackError(f"{where}: {b.kind} branch needs a default message")
    if b.kind == "action":
        if b.risk < 1:
            raise PackError(f"{where}: an action changes state, risk must be >= 1")
        if not d.get("verify"):
            raise PackError(f"{where}: an action needs a verify step (a read-only check that it worked)")
        b.verify = _step(d["verify"], where, "check")
    elif b.risk != 0:
        raise PackError(f"{where}: only action branches may have risk > 0")
    return b


def load_pack(root: Path) -> Pack:
    meta = yaml.safe_load((root / "pack.yaml").read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or not isinstance(meta.get("name"), str) or not ID_RE.match(f"x.{meta['name']}"):
        raise PackError(f"{root}/pack.yaml: needs a name (lower-case letters, digits, dashes)")
    name = meta["name"]
    status = str(meta.get("status", "draft"))
    if status not in STATUSES:
        raise PackError(f"{name}: status must be one of {STATUSES}")
    critical = bool(meta.get("safety_critical", False))
    if status == "reviewed" and not meta.get("reviewed_by"):
        raise PackError(f"{name}: status reviewed needs reviewed_by (who checked it)")
    branches = [load_branch(p, name) for p in sorted((root / "branches").glob("*.yaml"))] \
        if (root / "branches").is_dir() else []
    safety = [SafetyRule(r["id"], re.compile(r["pattern"], re.I), r["response"])
              for r in meta.get("safety", [])]
    near: dict[str, list[str]] = {}
    nm = root / "near-misses.yaml"
    if nm.is_file():
        raw = yaml.safe_load(nm.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise PackError(f"{name}/near-misses.yaml: must map topic -> list of phrasings")
        for topic, pats in raw.items():
            if not isinstance(pats, list) or not all(isinstance(x, str) for x in pats):
                raise PackError(f"{name}/near-misses.yaml: {topic} must be a list of strings")
            near[str(topic)] = pats
    pack = Pack(name=name, version=str(meta.get("version", "0")), branches=branches,
                synonyms={k: list(v) for k, v in (meta.get("synonyms") or {}).items()}, safety=safety,
                near_misses=near, description=str(meta.get("description", "")), domain=str(meta.get("domain", "")),
                status=status, safety_critical=critical, sources=list(meta.get("sources", []) or []),
                reviewed_by=str(meta.get("reviewed_by", "") or ""), root=str(root),
                other_devices=[str(x).lower() for x in meta.get("other_devices", []) or []],
                this_device=[str(x).lower() for x in meta.get("this_device", []) or []])
    if critical:
        if not pack.sources:
            raise PackError(f"{name}: a safety-critical pack needs pack-level sources")
        for b in branches:
            if b.kind == "answer" and not b.sources:
                raise PackError(f"{name}/{b.id}: safety-critical answers need cited sources")
    seen: set[str] = set()
    for b in branches:
        if b.id in seen:
            raise PackError(f"{name}: duplicate branch id {b.id}")
        seen.add(b.id)
    return pack


def load_packs(paths: list[Path]) -> list[Pack]:
    packs = [load_pack(p) for p in paths]
    ids: dict[str, str] = {}
    for p in packs:
        for b in p.branches:
            if b.id in ids:
                raise PackError(f"branch {b.id} defined in both {ids[b.id]} and {p.name}")
            ids[b.id] = p.name
    return packs
