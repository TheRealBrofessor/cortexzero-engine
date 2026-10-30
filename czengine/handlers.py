"""Handlers turn a routed branch into an answer. Only fixed branch steps ever run, never a shell.

answer : fixed text
check  : read-only steps, rules pick the message from their outputs
action : changes state; preview + explicit consent, then run and verify
Slots ({host}, {port}) are pulled from the user's text by code and validated by type. A later step
may use an earlier step's value ({gw}) only if it looks like an address or name (CHAIN_VALUE).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from czengine.library import CHAIN_VALUE, SLOT_TYPES, Branch, Rule, Step
from czengine.runners import LocalRunner, RawResult

HOST_IN_TEXT = re.compile(r"\b((?:[a-z0-9-]+\.)+[a-z]{2,}|\d{1,3}(?:\.\d{1,3}){3})\b", re.I)
PORT_IN_TEXT = re.compile(r"\bport\s*(\d{1,5})\b|:(\d{1,5})\b", re.I)


@dataclass
class StepResult:
    id: str
    argv: list[str]
    rc: int
    out: str                      # after extraction/formatting (what rules and messages see)
    raw: str
    seconds: float


@dataclass
class Outcome:
    branch_id: str
    kind: str
    message: str
    status: str   # answered | checked | needs_consent | done | verify_failed | needs_slot
    steps: list[StepResult] = field(default_factory=list)
    preview: list[str] = field(default_factory=list)


def public_host(val: str) -> bool:
    """A host a check may contact: a public IP literal, or a name that isn't obviously local."""
    import ipaddress
    try:
        return ipaddress.ip_address(val).is_global
    except ValueError:
        pass
    v = val.lower().rstrip(".")
    return not (v == "localhost" or v.endswith((".localhost", ".local", ".lan", ".home", ".internal", ".arpa")))


def extract_slots(branch: Branch, text: str) -> tuple[dict[str, str], list[str]]:
    values, missing = {}, []
    for name, typ in branch.slots.items():
        val = None
        if typ == "host":
            m = HOST_IN_TEXT.search(text.replace("https://", " ").replace("http://", " "))
            val = m.group(1).lower() if m else None
            if val is not None and not public_host(val):
                val = None    # never point a check at loopback, link-local or private addresses
        elif typ == "port":
            m = PORT_IN_TEXT.search(text)
            val = (m.group(1) or m.group(2)) if m else None
            if val is not None and not 1 <= int(val) <= 65535:
                val = None
        if val is None or not SLOT_TYPES[typ].match(val):
            missing.append(name)
        else:
            values[name] = val
    return values, missing


def resolve_argv(argv: list[str], values: dict[str, str], known: set[str] | None = None) -> list[str] | None:
    """Fill {name} placeholders inside argv items. Only `known` names (declared slots and earlier step
    ids) are placeholders, so tool syntax like curl's %{http_code} passes through untouched.
    None if a needed value is missing or unsafe."""
    out = []
    for a in argv:
        names = [n for n in re.findall(r"\{(\w+)\}", a) if known is None or n in known]
        for n in names:
            v = values.get(n)
            if v is None or not CHAIN_VALUE.match(v):
                return None
            a = a.replace("{" + n + "}", v)
        out.append(a)
    return out


def postprocess(step: Step, raw: str) -> str:
    text = raw
    if step.extract:
        lines = []
        for m in re.finditer(step.extract, raw, re.M):
            groups = [g or "" for g in m.groups()] or [m.group(0)]
            line = step.fmt.format(None, *groups) if step.fmt else (groups[0] if m.groups() else m.group(0))
            if line not in lines:
                lines.append(line.strip())
            if step.first_only:
                break
        text = "\n".join(lines)
    if step.transform == "mbps_from_bytes_per_s":
        try:
            text = f"{float(text.strip()) * 8 / 1e6:.1f}"
        except ValueError:
            text = "0.0"
    return "\n".join(text.splitlines()[: step.max_lines]).strip()


def _fires(rule: Rule, res: dict[str, StepResult]) -> bool:
    r = res.get(rule.step) if rule.step else None
    if rule.step and r is None:
        return False
    if rule.rc_nonzero is not None and (r.rc != 0) != rule.rc_nonzero:
        return False
    if rule.empty is not None and (r.out == "") != rule.empty:
        return False
    if rule.contains is not None and rule.contains.lower() not in r.out.lower():
        return False
    if rule.missing is not None and rule.missing.lower() in r.out.lower():
        return False
    if rule.matches is not None and not re.search(rule.matches, r.out, re.I | re.M):
        return False
    return True


def fill(message: str, res: dict[str, StepResult], slots: dict[str, str]) -> str:
    """{step} = first output line, {step:all} = whole output, {slot} = slot value."""
    def sub(m):
        key, whole = m.group(1), m.group(2)
        if key in slots:
            return slots[key]
        r = res.get(key)
        if r is None:
            return m.group(0)
        if not r.out:
            return "(no output)"
        return r.out if whole else r.out.splitlines()[0]
    return re.sub(r"\{(\w+)(:all)?\}", sub, message)


def cite(branch: Branch) -> str:
    return ("\nSources: " + "; ".join(branch.sources)) if branch.sources else ""


class Handlers:
    def __init__(self, runner=None, online=None):
        self.runner = runner or LocalRunner()
        self.online = online   # OnlineSource or None (offline: the default)

    def run_step(self, step: Step, values: dict[str, str], mode: str, known: set[str]) -> StepResult:
        argv = resolve_argv(step.argv, values, known)
        if argv is None:
            return StepResult(step.id, step.argv, 2, "", "skipped: an input value was missing or unsafe", 0.0)
        r: RawResult = self.runner.run(argv, step.timeout, mode)
        return StepResult(step.id, argv, r.rc, postprocess(step, r.out), r.out, r.seconds)

    def handle(self, branch: Branch, text: str, consent: bool = False) -> Outcome:
        if branch.kind == "answer":
            msg = branch.text.strip() + cite(branch)
            got = self.online.get(branch.online) if self.online and branch.online else None
            if got:
                excerpt, at = got
                msg += (f"\n\nOnline (fetched {time.strftime('%Y-%m-%d', time.localtime(at))} from {branch.online}, "
                        f"not checked by the pack author): {excerpt}")
            return Outcome(branch.id, "answer", msg, "answered")
        slots, missing = extract_slots(branch, text)
        if missing:
            return Outcome(branch.id, branch.kind, f"I need: {', '.join(missing)} "
                           "(for example a site name like example.com, or a port number).", "needs_slot")
        if branch.kind == "action" and not consent:
            known = set(branch.slots) | {s.id for s in branch.steps}
            preview = [" ".join(resolve_argv(s.argv, slots, known) or s.argv) for s in branch.steps]
            return Outcome(branch.id, "action", f"{branch.title}: this will run the commands below "
                           f"(risk level {branch.risk}). Reply 'yes' to run, anything else to cancel.",
                           "needs_consent", preview=preview)
        known = set(branch.slots) | {s.id for s in branch.steps}
        values, results = dict(slots), {}
        stopped = None
        for s in branch.steps:
            r = self.run_step(s, values, branch.kind, known)
            results[s.id] = r
            if r.out:
                values.setdefault(s.id, r.out.splitlines()[0])
            if branch.kind == "action" and r.rc != 0 and not s.allow_fail:
                stopped = r       # never keep changing things after a failed step
                break
        fired = next((rule for rule in branch.rules if _fires(rule, results)), None)
        msg = fill(fired.say if fired else branch.default, results, slots)
        status = "checked" if branch.kind == "check" else "done"
        if fired and fired.failed:
            status = "error"
        if stopped is not None:
            status = "error"
            if not (fired and fired.failed):
                msg = (f"Step `{' '.join(stopped.argv)}` failed (exit {stopped.rc}): {stopped.raw[:200]}\n"
                       "I stopped there; nothing after it was run.")
        elif branch.kind == "action" and branch.verify:
            v = self.run_step(branch.verify, values, "check", known)
            results[v.id] = v
            if v.rc != 0:
                status, msg = "verify_failed", msg + "\nThe follow-up check did not pass, so this is not fixed yet."
        return Outcome(branch.id, branch.kind, msg.strip(), status, list(results.values()))
