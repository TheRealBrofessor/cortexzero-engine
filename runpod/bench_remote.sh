#!/bin/bash
# One command: start a GPU pod, run the routing benchmark against the 1B routers on it, always shut it down.
#   runpod/bench_remote.sh [bench case files...]      (default: bench/cases-dev.yaml)
# Cost cap: cheapest in-stock GPU <= $0.40/hr, auto-terminate after 180 min even if this script dies.
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${CZ_PYTHON:-python3}"
trap '"$PY" runpod/pod.py down' EXIT
"$PY" runpod/pod.py up --max-price "${MAX_PRICE:-0.40}" --max-minutes "${MAX_MINUTES:-180}" || exit 1
for f in "${@:-bench/cases-dev.yaml}"; do
  echo "== $f"
  "$PY" bench/run_bench.py "$f" --backend openai --url http://127.0.0.1:18000 --models lfm2.5-1.2b gemma3-1b llama3.2-1b
done
