#!/usr/bin/env python3
"""RunPod control for CortexZero GPU runs. Nothing is billed until `up` (or `create`) is run.

  pod.py plan  [--max-price 0.40]        show which in-stock GPU `up` would pick, and the cost cap (no charge)
  pod.py up    [--max-price 0.40] [--max-minutes 180]
                 create pod (cheapest in-stock GPU <= max price, community then secure), start the
                 local auto-terminate watchdog, run boot.sh (llama.cpp + GGUF routers), open SSH tunnel
                 -> OpenAI-compatible router server at http://127.0.0.1:18000
  pod.py up --no-boot ...                 same, but only wait for SSH (fine-tuning: runpod/finetune.sh drives it)
  pod.py down                             terminate pod, close tunnel, stop watchdog
  pod.py status                           current pod (from runpod/.state.json) and account spend
No network volume is ever created; terminating deletes everything. The model server binds to the
pod's 127.0.0.1 and is reached only through the SSH tunnel (never a public proxy URL).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / ".state.json"
API = "https://api.runpod.io/graphql"
IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
KEY = Path.home() / ".ssh/cz_runpod_ed25519"
KNOWN = Path.home() / ".ssh/cz_runpod_known_hosts"
TUNNEL_PORT = 18000
MIN_VRAM_GB = 16


def api_key() -> str:
    """RUNPOD_API_KEY from the environment, or from a KEY=VALUE file named by RUNPOD_ENV_FILE. Never printed."""
    if os.environ.get("RUNPOD_API_KEY"):
        return os.environ["RUNPOD_API_KEY"].strip()
    env_file = os.environ.get("RUNPOD_ENV_FILE")
    if env_file and Path(env_file).expanduser().is_file():
        for line in Path(env_file).expanduser().read_text().splitlines():
            if line.startswith("RUNPOD_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("Set RUNPOD_API_KEY (or RUNPOD_ENV_FILE pointing to a file with RUNPOD_API_KEY=...)")


def gql(query: str, variables: dict | None = None) -> dict:
    req = urllib.request.Request(API, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key()}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    if d.get("errors"):
        raise RuntimeError(str(d["errors"]))
    return d["data"]


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=1))


def candidates(max_price: float) -> list[tuple[float, str, str, int]]:
    """In-stock single GPUs with >= MIN_VRAM_GB, priced <= max_price, cheapest first: (price, cloud, id, vram)."""
    out = []
    for cloud, secure in (("SECURE", "true"), ("COMMUNITY", "false")):  # secure first: reliable public IP for SSH
        q = ("query { gpuTypes { id memoryInGb lowestPrice(input:{gpuCount:1, secureCloud:%s})"
             " { uninterruptablePrice stockStatus } } }" % secure)
        for g in gql(q)["gpuTypes"]:
            lp = g.get("lowestPrice") or {}
            price = lp.get("uninterruptablePrice")
            if lp.get("stockStatus") and price and price <= max_price and (g.get("memoryInGb") or 0) >= MIN_VRAM_GB:
                out.append((price, cloud, g["id"], g["memoryInGb"]))
    return sorted(out, key=lambda c: (c[1] != "SECURE", c[0]))


def plan(max_price: float, max_minutes: int) -> None:
    c = candidates(max_price)
    if not c:
        print(f"No in-stock GPU <= ${max_price}/hr right now.")
        return
    print(f"In stock <= ${max_price}/hr (cheapest first; `up` tries them in this order):")
    for price, cloud, gid, mem in c[:8]:
        print(f"  ${price:.2f}/hr  {cloud:9}  {gid}  ({mem} GB)  -> worst case ${price * max_minutes / 60:.2f} "
              f"for {max_minutes} min")
    bal = gql("query { myself { clientBalance currentSpendPerHr pods { id } } }")["myself"]
    print(f"Balance ${bal['clientBalance']:.2f}; running pods: {len(bal['pods'])}; spend now ${bal['currentSpendPerHr']}/hr")


def ssh_base(ip: str, port: int) -> list[str]:
    return ["ssh", "-i", str(KEY), "-o", f"UserKnownHostsFile={KNOWN}", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30", "-p", str(port), f"root@{ip}"]


def terminate(pod_id: str) -> None:
    gql("mutation($id: String!) { podTerminate(input: {podId: $id}) }", {"id": pod_id})
    print(f"terminated {pod_id}")


def up(max_price: float, max_minutes: int, boot: bool = True) -> None:
    if load_state().get("pod_id"):
        raise SystemExit("A pod is already recorded in .state.json; run `pod.py down` first.")
    pub = KEY.with_suffix(".pub").read_text().strip()
    pod = None
    for price, cloud, gid, mem in candidates(max_price):
        inp = {"cloudType": cloud, "gpuCount": 1, "gpuTypeId": gid, "name": "cz-router", "imageName": IMAGE,
               "containerDiskInGb": 40, "volumeInGb": 0, "ports": "22/tcp", "supportPublicIp": True,
               "env": [{"key": "PUBLIC_KEY", "value": pub}]}
        try:
            pod = gql("mutation($in: PodFindAndDeployOnDemandInput) { podFindAndDeployOnDemand(input: $in) "
                      "{ id costPerHr machine { gpuDisplayName } } }", {"in": inp})["podFindAndDeployOnDemand"]
            break
        except RuntimeError as e:
            print(f"  {gid} ({cloud}) unavailable: {'SUPPLY' if 'SUPPLY' in str(e) else e}")
    if not pod:
        raise SystemExit("No pod could be created within the price cap.")
    print(f"created {pod['id']} on {pod['machine']['gpuDisplayName']} at ${pod['costPerHr']}/hr "
          f"(auto-terminate in {max_minutes} min)", flush=True)
    wd = subprocess.Popen([sys.executable, __file__, "_watchdog", pod["id"], "--max-minutes", str(max_minutes)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    state = {"pod_id": pod["id"], "cost_per_hr": pod["costPerHr"], "watchdog_pid": wd.pid, "started": time.time()}
    save_state(state)
    try:
        for _ in range(150):  # image pull can take a while on a fresh host
            st = gql("query($id: String!) { pod(input: {podId: $id}) { runtime { ports { ip isIpPublic privatePort "
                     "publicPort } } } }", {"id": pod["id"]})["pod"]
            ports = [p for p in ((st.get("runtime") or {}).get("ports") or []) if p["privatePort"] == 22 and p["isIpPublic"]]
            if ports:
                state.update(ip=ports[0]["ip"], port=ports[0]["publicPort"])
                break
            time.sleep(10)
        else:
            raise RuntimeError("SSH port never appeared")
        save_state(state)
        base = ssh_base(state["ip"], state["port"])
        for _ in range(30):
            if subprocess.run(base + ["true"], capture_output=True).returncode == 0:
                break
            time.sleep(10)
        else:
            raise RuntimeError("SSH never answered")
        if not boot:
            print(f"READY: ssh root@{state['ip']} -p {state['port']} (no boot script run)", flush=True)
            return
        print("running boot.sh (llama.cpp build + model download, ~5-10 min)...", flush=True)
        with open(HERE / "boot.sh") as fh:
            r = subprocess.run(base + ["bash -s"], stdin=fh, capture_output=True, text=True, timeout=2400)
        print(r.stdout.strip()[-600:])
        if "lfm2.5-1.2b" not in r.stdout:
            raise RuntimeError("model server did not come up; see pod /tmp/build.log /tmp/server.log")
        tun = subprocess.Popen(["ssh", "-N", "-i", str(KEY), "-o", f"UserKnownHostsFile={KNOWN}",
                                "-o", "ServerAliveInterval=30", "-o", "ExitOnForwardFailure=yes",
                                "-L", f"{TUNNEL_PORT}:127.0.0.1:8000", "-p", str(state["port"]), f"root@{state['ip']}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        state["tunnel_pid"] = tun.pid
        save_state(state)
        time.sleep(3)
        print(f"READY: router server at http://127.0.0.1:{TUNNEL_PORT} (models: lfm2.5-1.2b, gemma3-1b, llama3.2-1b)")
    except Exception as e:
        print(f"up failed ({e}); terminating to stop billing", flush=True)
        down()
        raise SystemExit(1)


def _kill(pid) -> None:
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass


def pod_exists(pod_id: str) -> bool:
    pods = gql("query { myself { pods { id } } }")["myself"]["pods"]
    return any(p["id"] == pod_id for p in pods)


def down() -> None:
    s = load_state()
    if s.get("pod_id"):
        gone = False
        for attempt in range(5):  # never forget a pod that may still be billing
            try:
                terminate(s["pod_id"])
            except Exception as e:
                print(f"terminate error (attempt {attempt + 1}): {e}")
            try:
                gone = not pod_exists(s["pod_id"])
            except Exception as e:
                print(f"status check error: {e}")
            if gone:
                break
            time.sleep(10 * (attempt + 1))
        if not gone:
            print(f"POD {s['pod_id']} MAY STILL BE RUNNING: state and watchdog kept; run `pod.py down` again "
                  "or terminate it in the RunPod console.")
            _kill(s.get("tunnel_pid"))
            raise SystemExit(1)
        if s.get("started"):
            mins = (time.time() - s["started"]) / 60
            print(f"ran ~{mins:.0f} min, est. cost ${mins / 60 * float(s.get('cost_per_hr') or 0):.2f}")
    _kill(s.get("tunnel_pid"))
    _kill(s.get("watchdog_pid"))
    if STATE.exists():
        STATE.unlink()
    bal = gql("query { myself { clientBalance currentSpendPerHr pods { id } } }")["myself"]
    print(f"Balance ${bal['clientBalance']:.2f}; running pods: {len(bal['pods'])}; spend now ${bal['currentSpendPerHr']}/hr")


def status() -> None:
    print(json.dumps(load_state(), indent=1) or "{}")
    bal = gql("query { myself { clientBalance currentSpendPerHr pods { id name desiredStatus } } }")["myself"]
    print(json.dumps(bal, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "up", "down", "status", "_watchdog"])
    ap.add_argument("pod_id", nargs="?")
    ap.add_argument("--max-price", type=float, default=0.40)
    ap.add_argument("--max-minutes", type=int, default=180)
    ap.add_argument("--no-boot", action="store_true", help="up: only wait for SSH, don't run boot.sh")
    a = ap.parse_args()
    if a.cmd == "plan":
        plan(a.max_price, a.max_minutes)
    elif a.cmd == "up":
        up(a.max_price, a.max_minutes, boot=not a.no_boot)
    elif a.cmd == "down":
        down()
    elif a.cmd == "status":
        status()
    else:  # _watchdog: terminate after max_minutes regardless of this session
        time.sleep(a.max_minutes * 60)
        for attempt in range(20):  # keep trying: a transient API error must not leave the pod billing
            try:
                terminate(a.pod_id)
            except Exception:
                pass
            try:
                if not pod_exists(a.pod_id):
                    break
            except Exception:
                pass
            time.sleep(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
