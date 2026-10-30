"""Engine tests that need no model and run no real system commands (a fake runner is injected)."""
from pathlib import Path

import pytest

from czengine.engine import Engine
from czengine.handlers import extract_slots, postprocess, resolve_argv
from czengine.runners import RawResult
from czengine.library import PackError, load_branch
from czengine.normalize import Normalizer

ROOT = Path(__file__).resolve().parents[1]
PACKS = [ROOT / "packs/home-network", ROOT / "packs/safety"]


class FakeRunner:
    """outputs: {"argv prefix": (rc, raw_output)}; longest matching prefix wins. Default: rc 0, empty."""
    def __init__(self, outputs=None):
        self.outputs, self.ran = outputs or {}, []

    def run(self, argv, timeout, mode):
        cmd = " ".join(argv)
        self.ran.append((mode, cmd))
        keys = sorted((k for k in self.outputs if cmd.startswith(k)), key=len, reverse=True)
        rc, out = self.outputs[keys[0]] if keys else (0, "")
        return RawResult(argv, rc, out, 0.0)


class FakeModel:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def choose(self, question, options, context=""):
        from czengine.model import ModelChoice
        self.calls += 1
        ids = [o[0] for o in options]
        pick = self.answer if self.answer in ids + ["none", "ask"] else None
        return ModelChoice(pick, '{"choice": "%s"}' % self.answer, 0.01, "" if pick else "invalid choice")


def engine(runner=None, model=None):
    return Engine.from_dirs(PACKS, model=model, runner=runner or FakeRunner())


def test_clear_question_routes_by_index_without_model():
    m = FakeModel("net.speed")
    t = engine(model=m).ask("what's my ip address")
    assert t.route["branch_id"] == "net.my-ip" and t.route["decided_by"] == "index" and m.calls == 0


def test_out_of_scope_is_refused_not_guessed():
    t = engine().ask("write me a poem about my dog")
    assert t.label.startswith("∅") and t.route["branch_id"] is None


def test_safety_runs_first_and_never_routes():
    r = FakeRunner()
    t = engine(runner=r).ask("I want to end my life")
    assert t.label == "⚠ safety" and "988" in t.answer and r.ran == []


def test_model_cannot_pick_an_unoffered_branch():
    t = engine(model=FakeModel("rm-rf-everything")).ask("how do I set up a raspberry pi as a nas")
    assert t.route["branch_id"] is None


def test_typos_are_fixed_toward_library_words():
    t = engine().ask("my wifi says conected but no internt")
    assert t.route["branch_id"] == "net.no-internet" and t.corrections.get("conected") == "connected"


def test_valid_english_words_are_not_corrected():
    n = Normalizer({"want", "remote"})
    assert n("he wants remote access").corrections == {}


HEALTHY = {"ip -br link": (0, "lo UNKNOWN 00:00\nwlp1s0 UP aa:bb:cc:dd:ee:ff"),
           "ip -4 -br addr": (0, "lo UNKNOWN 127.0.0.1/8\nwlp1s0 UP 192.168.1.20/24"),
           "ip route show default": (0, "default via 192.168.1.1 dev wlp1s0")}


def test_check_rules_pick_message_from_evidence():
    r = FakeRunner({**HEALTHY, "ping -c 2 -W 2 1.1.1.1": (1, "100% packet loss")})
    t = engine(runner=r).ask("connected but no internet")
    assert "provider" in t.answer
    assert ("check", "ping -c 2 -W 2 192.168.1.1") in r.ran      # gateway value chained into argv


def test_healthy_ladder_reports_router_address():
    t = engine(runner=FakeRunner(HEALTHY)).ask("connected but no internet")
    assert "192.168.1.1" in t.answer and "Everything checks out" in t.answer


def test_unsafe_chained_value_is_not_used():
    assert resolve_argv(["ping", "{gw}"], {"gw": "1.2.3.4; rm -rf /"}) is None
    assert resolve_argv(["ping", "{gw}"], {"gw": "10.0.0.1"}) == ["ping", "10.0.0.1"]
    assert resolve_argv(["curl", "-w", "%{http_code}"], {}, {"host"}) == ["curl", "-w", "%{http_code}"]


def test_branch_files_may_not_contain_shell_strings(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("id: net.x-y\ntitle: t\ndescription: d\nkind: check\npatterns: [a b, c d]\n"
                 "steps: [{id: s, run: 'ip a | grep x'}]\ndefault: ok\n")
    with pytest.raises(PackError):
        load_branch(p, "test")


def test_action_needs_consent_then_runs_under_action_policy_and_verifies():
    r = FakeRunner()
    e = engine(runner=r)
    t1 = e.ask("flush my dns cache")
    assert t1.label == "? needs your OK" and r.ran == []          # nothing ran before consent
    t2 = e.ask("yes")
    assert t2.route["decided_by"] == "consent"
    assert ("action", "resolvectl flush-caches") in r.ran and ("check", "getent hosts example.com") in r.ran


def test_action_reports_failed_verification():
    r = FakeRunner({"getent hosts example.com": (2, "")})
    e = engine(runner=r)
    e.ask("flush my dns cache")
    t = e.ask("yes")
    assert t.label == "⚠ not fixed"


def test_slots_are_validated_and_shell_quoted():
    b = next(b for b in engine().index.branches if b.id == "net.site-check")
    vals, missing = extract_slots(b, "is example.com; rm -rf / down")
    assert vals == {"host": "example.com"} and not missing
    _, missing = extract_slots(b, "is the site down")
    assert missing == ["host"]


def test_pack_validation_rejects_unsafe_shapes(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("id: net.x-y\ntitle: t\ndescription: d\nkind: action\npatterns: [a b, c d]\n"
                 "steps: [{id: s, argv: ['true']}]\ndefault: ok\nrisk: 0\n")
    with pytest.raises(PackError):
        load_branch(p, "test")  # an action must declare risk >= 1


def test_type_ahead_suggestions():
    s = engine().suggest("how fast is my inter")
    assert any("speed" in x.lower() for x in s)


def test_extraction_formats_matches():
    from czengine.library import Step
    st = Step("x", ["ip"], extract=r"^(?!lo\b)(\S+)\s+\S+\s+(\S+)", fmt="{1}: {2}")
    assert postprocess(st, "lo UNKNOWN 127.0.0.1/8\nwlan0 UP 10.0.0.5/24") == "wlan0: 10.0.0.5/24"


def test_core_gate_blocks_branch_without_its_concept():
    e = engine()
    assert e.index.eligible("net.wifi-password-change", set(e.index.terms("change my wifi password")))
    assert not e.index.eligible("net.wifi-password-change", set(e.index.terms("reset my gmail password")))


@pytest.mark.parametrize("q", ["which router bit gives a clean round edge on oak",
                               "what port does our cruise ship dock at in Nassau",
                               "I forgot my gmail password, how do I reset it",
                               "how do i share the wifi password from my iphone to my moms android phone"])
def test_near_miss_out_of_scope_is_refused(q):
    t = engine().ask(q)
    assert t.route["branch_id"] is None, t.route


def test_inflections_match_library_words():
    e = engine()
    assert e.index.stem("renewing") == "renew" and e.index.stem("forwarded") == "forward"


def test_every_core_word_can_match():
    e = engine()
    for b in e.index.branches:
        for g in b.core:
            for w in g:
                assert e.index.terms(w) == [e.index.canon.get(w, w)], (b.id, w)


def test_compiled_packs_route_like_yaml_and_go_stale_on_edit(tmp_path):
    import shutil
    from czengine.compiled import compile_packs, load_compiled
    from czengine.index import Index
    from czengine.library import load_packs
    dirs = [tmp_path / "home-network", tmp_path / "safety"]
    for src, dst in zip(PACKS, dirs):
        shutil.copytree(src, dst)
    out = tmp_path / "c.json"
    compile_packs(dirs, out)
    packs, idx = load_compiled(dirs, out)
    ref = Index(load_packs(dirs))
    for q in ["my wifi keeps dropping", "is port 22 open", "router bit for oak", "whats my public ip"]:
        assert [(c.branch.id, c.score) for c in idx.search(q)] == [(c.branch.id, c.score) for c in ref.search(q)]
        assert idx.near_miss(q) == ref.near_miss(q)
    assert Engine.from_dirs(dirs, compiled=out).compiled
    (dirs[0] / "branches/mac.yaml").write_text((dirs[0] / "branches/mac.yaml").read_text() + "\n# edit\n")
    assert load_compiled(dirs, out) is None and not Engine.from_dirs(dirs, compiled=out).compiled


def test_online_source_is_off_by_default_and_cited_when_on(tmp_path):
    from czengine.online import OnlineSource
    e = engine()
    b = e.by_id["net.router-restart"]
    b.online = "https://example.org/router"
    assert "Online" not in e.ask("how do i restart my router").answer
    calls = []
    src = OnlineSource(tmp_path, fetch=lambda u: calls.append(u) or "<html><script>x()</script><p>Wait 30 s.</p></html>")
    e2 = Engine.from_dirs(PACKS, runner=FakeRunner(), online=src)
    e2.by_id["net.router-restart"].online = "https://example.org/router"
    a = e2.ask("how do i restart my router").answer
    assert "Wait 30 s." in a and "https://example.org/router" in a and "x()" not in a
    e2.ask("how do i restart my router")
    assert len(calls) == 1          # second time from cache


def test_safety_critical_pack_needs_sources(tmp_path):
    root = tmp_path / "crit"
    (root / "branches").mkdir(parents=True)
    (root / "pack.yaml").write_text("name: crit\nstatus: unverified\nsafety_critical: true\nsources: [https://x.org]\n")
    (root / "branches/a.yaml").write_text("id: crit.a\ntitle: A\nkind: answer\ndescription: d\npatterns: [a b, c d]\ntext: t\n")
    from czengine.library import load_pack
    with pytest.raises(PackError, match="cited sources"):
        load_pack(root)
    (root / "pack.yaml").write_text("name: crit\nstatus: reviewed\nsources: [https://x.org]\n")
    with pytest.raises(PackError, match="reviewed_by"):
        load_pack(root)


def test_pack_new_scaffold_validates(tmp_path):
    from czengine.packtools import lint, new_pack
    root = new_pack("demo-pack", tmp_path)
    errors, _ = lint([root])
    assert errors == []


CAMP = [ROOT / "packs/family-camping", ROOT / "packs/safety"]
# The family-camping pack is a safety-critical draft that isn't published until reviewed; its tests run only where it exists.
needs_camp = pytest.mark.skipif(not (ROOT / "packs/family-camping/pack.yaml").exists(),
                                reason="family-camping draft pack not present")


@needs_camp
@pytest.mark.parametrize("q,rule", [("my son got bit by a rattlesnake", "snakebite"),
                                    ("I can't find my daughter anywhere at the campground", "lost-child"),
                                    ("my friend was struck by lightning", "struck-by-lightning")])
def test_camping_emergencies_hit_safety_rules_first(q, rule):
    r = FakeRunner()
    t = Engine.from_dirs(CAMP, runner=r).ask(q)
    assert t.route == {"decided_by": "safety", "rule": rule} and "911" in t.answer


@needs_camp
def test_camping_answers_cite_sources_and_pack_is_unverified():
    e = Engine.from_dirs(CAMP, runner=FakeRunner())
    assert e.packs[0].status == "unverified" and e.packs[0].safety_critical
    t = e.ask("how do i put out the campfire")
    assert t.route["branch_id"] == "camp.campfire-out" and "Sources: https://" in t.answer


# ---- regressions for the independent (Codex) review, 2026-10-30 ----

def _branch_yaml(tmp_path, steps, kind="check", extra=""):
    p = tmp_path / "b.yaml"
    p.write_text(f"id: t.x\ntitle: T\nkind: {kind}\ndescription: d\npatterns: [a b, c d]\ndefault: ok\n"
                 f"{'risk: 1' if kind == 'action' else ''}\nsteps:\n{steps}{extra}")
    return p


@pytest.mark.parametrize("argv", ['[sh, -c, "printf X"]', '[python3, -c, "1"]', '[env, ls]', '[rm, -rf, /tmp/x]',
                                  '[nmcli, radio, wifi, "off"]', '[resolvectl, flush-caches]', '[cat, /etc/shadow]',
                                  '[curl, -o, /tmp/x, https://e.com]'])
def test_check_steps_cannot_run_shells_or_change_state(tmp_path, argv):
    with pytest.raises(PackError):
        load_branch(_branch_yaml(tmp_path, f"- id: s\n  argv: {argv}\n"), "t")


def test_runner_refuses_disallowed_argv_even_if_loader_was_bypassed():
    from czengine.runners import LocalRunner
    r = LocalRunner().run(["sh", "-c", "echo hi"], 5, "check")
    assert r.rc == 126 and "policy" in r.out


def test_action_needs_verify_and_bad_fmt_is_rejected(tmp_path):
    with pytest.raises(PackError, match="verify"):
        load_branch(_branch_yaml(tmp_path, "- id: s\n  argv: [resolvectl, flush-caches]\n", kind="action"), "t")
    with pytest.raises(PackError, match="fmt"):
        load_branch(_branch_yaml(tmp_path, "- id: s\n  argv: [ip, -br, link]\n  extract: '(ok)'\n  fmt: '{2}'\n"), "t")


def test_safety_turn_cancels_pending_consent():
    r = FakeRunner()
    e = engine(runner=r)
    assert e.ask("flush dns").label.startswith("?")
    e.ask("call 911")
    t = e.ask("yes")
    assert t.route.get("decided_by") != "consent" and not any(m == "action" for m, _ in r.ran)


def test_failed_action_step_stops_and_is_not_success():
    r = FakeRunner({"nmcli radio wifi off": (1, "permission denied")})
    e = engine(runner=r)
    e.ask("reconnect my wifi")
    t = e.ask("yes")
    assert t.label.startswith("⚠") and not any("radio wifi on" in c for _, c in r.ran)


def test_hundred_percent_packet_loss_is_reported_as_loss():
    out = "20 packets transmitted, 0 received, 100% packet loss"
    t = engine(runner=FakeRunner({"ping -c 20": (1, out), "ping -c 10": (1, out)})).ask("is there packet loss")
    assert "Packet loss detected" in t.answer


def test_gigabit_ethernet_is_not_called_100mbps():
    r = FakeRunner({"nmcli -t -f DEVICE,TYPE,STATE dev": (0, "enp1s0:ethernet:connected"),
                    "cat /sys/class/net/enp1s0/speed": (0, "1000")})
    t = engine(runner=r).ask("why is my ethernet stuck at 100 mbps")
    assert "only at 100" not in t.answer and "1000" in t.answer


@pytest.mark.parametrize("q,bad", [("check if site 169.254.169.254 is up", "169.254.169.254"),
                                   ("is website 127.0.0.1 down", "127.0.0.1"),
                                   ("is 999.999.999.999 down", "999.999"), ("is port 99999 open", "99999")])
def test_private_hosts_and_impossible_slots_never_reach_a_command(q, bad):
    r = FakeRunner()
    engine(runner=r).ask(q)
    assert not any(bad in cmd for _, cmd in r.ran)


def test_chained_values_cannot_become_options():
    assert resolve_argv(["curl", "{next}"], {"next": "--output"}, {"next"}) is None


@needs_camp
def test_ambiguous_synonym_across_packs_does_not_misroute():
    e = Engine.from_dirs(PACKS + [ROOT / "packs/family-camping"], runner=FakeRunner())
    assert e.ask("video calls keep freezing").route["branch_id"] == "net.packet-loss"
    assert e.ask("zoom keeps freezing").route["branch_id"] == "net.packet-loss"
    assert e.ask("my son is freezing and shivering at the campsite").route["branch_id"] == "camp.hypothermia"


@needs_camp
@pytest.mark.parametrize("q,rule", [("my child cannot breathe", "emergency"), ("a snake just bit my child", "snakebite"),
                                    ("our kids are missing", "lost-child"),
                                    ("my tongue is swelling after a bee sting", "allergic-reaction")])
def test_emergency_wording_variants_hit_safety(q, rule):
    t = Engine.from_dirs(CAMP, runner=FakeRunner()).ask(q)
    assert t.route.get("rule") == rule


def test_shipped_packs_lint_clean():
    from czengine.packtools import lint
    errors, warns = lint(sorted(p for p in (ROOT / "packs").iterdir() if (p / "pack.yaml").exists()))
    assert errors == [] and warns == []


@pytest.mark.parametrize("q", ["How can I change the password on my phone's Wi-Fi?", "what is the mac address of my xbox",
                               "I need to change the DNS on my satellite dish's firmware"])
def test_settings_of_other_devices_are_not_this_computers(q):
    t = engine().ask(q)
    assert t.route["branch_id"] is None, t.route


def test_router_side_help_for_other_devices_still_routes():
    assert engine().ask("my xbox says strict nat").route["branch_id"] == "net.port-forward"
    assert engine().ask("what is the mac address of my laptop").route["branch_id"] == "net.mac"


def test_breathing_emergency_variants_with_core_safety_pack():
    for q in ["my child cannot breathe", "my dad has trouble breathing"]:
        assert engine().ask(q).route.get("rule") == "emergency"


def test_cross_pack_synonym_conflict_keeps_both_meanings(tmp_path):
    for name, head, branch, pats in [("pa", "lag", "pa.calls", ["video calls keep freezing", "calls lag"]),
                                     ("pb", "cold", "pb.cold", ["kid is freezing and shivering", "too cold at night"])]:
        root = tmp_path / name
        (root / "branches").mkdir(parents=True)
        (root / "pack.yaml").write_text(f"name: {name}\nsynonyms:\n  {head}: [freezing]\n")
        (root / "branches/b.yaml").write_text(f"id: {branch}\ntitle: T\nkind: answer\ndescription: d\n"
                                              f"patterns: {pats}\ntext: t\n")
    e = Engine.from_dirs([tmp_path / "pa", tmp_path / "pb"], runner=FakeRunner())
    assert e.index.conflicts == {"freezing": {"lag", "cold"}}
    assert e.ask("zoom calls keep freezing").route["branch_id"] == "pa.calls"
    assert e.ask("my kid is freezing and shivering").route["branch_id"] == "pb.cold"
