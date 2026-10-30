"""Execution policy: which programs a pack step may run, per mode. Enforced at pack load AND by the runner.

A step is one program with fixed argv (never a shell). On top of that:
- shells, interpreters and launchers are refused everywhere (they would turn argv back into a script);
- "check" steps (run without asking) may only use read-only programs/sub-commands from CHECK;
- "action" steps (run only after consent) may also use the state-changing forms in ACTION.
A new pack that needs another program needs a code change here, reviewed by a person.
"""
from __future__ import annotations

import re

NEVER = {"sh", "bash", "dash", "zsh", "ksh", "fish", "csh", "tcsh", "busybox", "env", "xargs", "sudo", "su", "doas",
         "pkexec", "nohup", "setsid", "chroot", "systemd-run", "eval", "exec", "watch", "nice", "ionice", "timeout",
         "stdbuf", "script", "expect", "python", "python3", "perl", "ruby", "node", "php", "lua", "tclsh", "awk",
         "gawk", "mawk", "sed", "find", "dd", "rm", "mv", "cp", "chmod", "chown", "kill", "pkill", "killall",
         "reboot", "shutdown", "ssh", "scp", "nc", "ncat", "socat", "telnet", "tee", "make"}
INTERPRETER = re.compile(r"^(python|perl|ruby|node|php|lua)[\d.]*$")


def _no(words):
    bad = set(words)
    return lambda args: not (bad & set(args))


def _cat(args):  # only read files under these prefixes; no options
    return len(args) == 1 and any(args[0].startswith(p) for p in ("/etc/resolv.conf", "/sys/class/net/", "/proc/net/")) \
        and ".." not in args[0]


def _curl(args):
    bad = {"-o", "--output", "-O", "--remote-name", "-T", "--upload-file", "-d", "--data", "--data-binary",
           "--data-raw", "-F", "--form", "-X", "--request", "-K", "--config", "-u", "--user", "-b", "--cookie",
           "-c", "--cookie-jar", "--create-dirs"}
    for i, a in enumerate(args):
        if a in {"-o", "--output"} and i + 1 < len(args) and args[i + 1] == "/dev/null":
            continue
        if i > 0 and args[i - 1] in {"-o", "--output"} and a == "/dev/null":
            continue
        if a in bad or a.split("=", 1)[0] in bad or a.startswith(("file:", "dict:", "gopher:", "ftp:", "scp:")):
            return False
    return True


def _openssl(args):
    return bool(args) and args[0] == "s_client" and not ({"-out", "-sess_out", "-keylogfile", "-msgfile"} & set(args))


CHECK = {
    "ip": _no({"add", "del", "delete", "set", "flush", "change", "replace", "append", "exec", "netns"}),
    "nmcli": _no({"on", "off", "up", "down", "connect", "disconnect", "delete", "modify", "add", "edit", "reload",
                  "import", "export", "load", "hostname", "general"}),
    "resolvectl": lambda a: (bool(a) and a[0] in {"status", "query", "statistics"})
    or (a[:1] in (["dns"], ["domain"]) and len(a) <= 2),  # show only; "dns IFACE SERVER" would set
    "systemctl": lambda a: bool(a) and a[0] in {"is-active", "is-enabled", "status", "is-failed"},
    "cat": _cat, "curl": _curl, "openssl": _openssl,
    "ping": lambda a: True, "getent": lambda a: bool(a) and a[0] in {"hosts", "ahosts", "ahostsv4", "services"},
    "ss": lambda a: True, "lpstat": lambda a: True, "tracepath": lambda a: True, "traceroute": lambda a: True,
    "sleep": lambda a: len(a) == 1 and a[0].isdigit() and int(a[0]) <= 30,
}
ACTION = {
    **CHECK,
    "nmcli": lambda a: CHECK["nmcli"](a) or a in (["radio", "wifi", "off"], ["radio", "wifi", "on"],
                                                   ["networking", "off"], ["networking", "on"]),
    "resolvectl": lambda a: CHECK["resolvectl"](a) or a == ["flush-caches"],
}


def allowed(argv: list[str], mode: str) -> str | None:
    """None if allowed, else the reason. `mode`: check | action."""
    if not argv:
        return "empty argv"
    prog, args = argv[0], argv[1:]
    if prog in NEVER or INTERPRETER.match(prog):
        return f"{prog!r} is a shell, interpreter or launcher: never allowed"
    table = ACTION if mode == "action" else CHECK
    rule = table.get(prog)
    if rule is None:
        return f"{prog!r} is not on the {mode} allowlist (czengine/policy.py)"
    if not rule(args):
        return f"{' '.join(argv)!r} is not an allowed {mode} form of {prog}"
    return None
