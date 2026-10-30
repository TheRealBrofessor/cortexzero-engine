#!/bin/bash
# Fine-tune the 1B routers on RunPod and bring back Q4_K_M GGUFs. COSTS MONEY: run only after the owner said "go".
#   runpod/finetune.sh [extra training case files...]
# Steps: build data locally (dev + dev2 + given training files; held-out/private refused) -> fetch llama.cpp
# source locally -> pod up (cheapest GPU <= $0.40/hr, watchdog terminates after MAX_MINUTES) -> upload ->
# train + merge + GGUF on the pod -> download GGUFs + metrics -> pod down (always, via trap).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${CZ_PYTHON:-python3}"
STAMP=$(date +%Y%m%dT%H%M%S)
OUT="runpod/ft-out/$STAMP"; mkdir -p "$OUT" runpod/cache
MODELS=${MODELS:-"LiquidAI/LFM2.5-1.2B-Instruct lfm|unsloth/Llama-3.2-1B-Instruct llama"}   # "HF_ID NAME|..."
"$PY" bench/make_router_data.py "$OUT/data.jsonl" bench/cases-dev.yaml bench/cases-dev2.yaml bench/cases-dev3.yaml bench/train-hermes-r3.yaml "$@"
TAR=runpod/cache/llama.cpp.tar.gz
if [ ! -s "$TAR" ]; then
  TAG=$(curl -s https://api.github.com/repos/ggml-org/llama.cpp/releases/latest | "$PY" -c "import json,sys;print(json.load(sys.stdin)['tag_name'])")
  curl -sfL -o "$TAR" "https://github.com/ggml-org/llama.cpp/archive/refs/tags/$TAG.tar.gz"; echo "$TAG" > runpod/cache/llama.cpp.tag
fi
echo "llama.cpp $(cat runpod/cache/llama.cpp.tag)" | tee "$OUT/versions.txt"
trap '"$PY" runpod/pod.py down 2>&1 | tee -a "$OUT/pod.log"' EXIT
"$PY" runpod/pod.py up --no-boot --max-price "${MAX_PRICE:-0.40}" --max-minutes "${MAX_MINUTES:-120}" | tee "$OUT/pod.log"
IP=$("$PY" -c "import json;print(json.load(open('runpod/.state.json'))['ip'])")
PORT=$("$PY" -c "import json;print(json.load(open('runpod/.state.json'))['port'])")
SSH=(ssh -i "$HOME/.ssh/cz_runpod_ed25519" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/cz_runpod_known_hosts" -o ServerAliveInterval=30 -p "$PORT" "root@$IP")
SCP=(scp -i "$HOME/.ssh/cz_runpod_ed25519" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/cz_runpod_known_hosts" -P "$PORT")
"${SSH[@]}" "mkdir -p /work"
"${SCP[@]}" "$OUT/data.train.jsonl" "$OUT/data.val.jsonl" runpod/finetune.py runpod/ft_remote.sh "root@$IP:/work/"
"${SCP[@]}" "$TAR" "root@$IP:/work/llama.cpp.tar.gz"
# Model list goes to the pod as a file (never spliced into a remote shell command); each line is validated.
: > "$OUT/models.txt"
IFS='|' read -ra SPECS <<< "$MODELS"
for spec in "${SPECS[@]}"; do
  [[ "$spec" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\ [a-z0-9-]+$ ]] || { echo "bad MODELS entry: $spec" >&2; exit 2; }
  echo "$spec" >> "$OUT/models.txt"
done
"${SCP[@]}" "$OUT/models.txt" "root@$IP:/work/models.txt"
# Run detached on the pod (an SSH drop must not kill training); poll until ALL_DONE or the deadline.
"${SSH[@]}" "cd /work && nohup bash ft_remote.sh models.txt > remote.log 2>&1 < /dev/null &"
DEADLINE=$(( $(date +%s) + ${TRAIN_MINUTES:-100} * 60 ))
while :; do
  sleep 60
  "${SSH[@]}" "tail -3 /work/remote.log" 2>/dev/null | tee "$OUT/remote.tail" || true
  "${SSH[@]}" "grep -q ALL_DONE /work/remote.log" 2>/dev/null && break
  [ "$(date +%s)" -gt "$DEADLINE" ] && { echo "training deadline reached; collecting what exists"; break; }
done
"${SCP[@]}" "root@$IP:/work/remote.log" "$OUT/remote.log" || true
"${SCP[@]}" "root@$IP:/work/cz-router-*-Q4_K_M.gguf" "root@$IP:/work/metrics-*.json" "root@$IP:/work/train-*.log" "root@$IP:/work/*.log" "$OUT/" || true
ls -la "$OUT"
