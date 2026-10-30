#!/bin/bash
# On the pod: build llama-cpp-python with CUDA (from PyPI, which is reachable) and fetch the same GGUF
# quantizations the local Ollama models use. Server binds to 127.0.0.1 only (reached via SSH tunnel).
set -uo pipefail
mkdir -p /models && cd /models
fetch() { [ -s "$2" ] || curl -sfL --retry 3 -o "$2" "https://huggingface.co/$1/resolve/main/$2" && echo "got $2 $(stat -c %s $2)"; }
( fetch LiquidAI/LFM2.5-1.2B-Instruct-GGUF LFM2.5-1.2B-Instruct-Q4_K_M.gguf
  fetch ggml-org/gemma-3-1b-it-GGUF gemma-3-1b-it-Q4_K_M.gguf
  fetch bartowski/Llama-3.2-1B-Instruct-GGUF Llama-3.2-1B-Instruct-Q8_0.gguf ) > /tmp/fetch.log 2>&1 &
export CUDACXX=/usr/local/cuda/bin/nvcc PATH=/usr/local/cuda/bin:$PATH
python3 -c "import llama_cpp" 2>/dev/null || CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=native" CMAKE_BUILD_PARALLEL_LEVEL=64 pip install -q "llama-cpp-python[server]" > /tmp/build.log 2>&1
echo "build rc=$?"
wait
cat /tmp/fetch.log
cat > /models/config.json <<'JSON'
{"host": "127.0.0.1", "port": 8000, "models": [
 {"model": "/models/LFM2.5-1.2B-Instruct-Q4_K_M.gguf", "model_alias": "lfm2.5-1.2b", "n_gpu_layers": -1, "n_ctx": 4096, "chat_format": null},
 {"model": "/models/gemma-3-1b-it-Q4_K_M.gguf", "model_alias": "gemma3-1b", "n_gpu_layers": -1, "n_ctx": 4096},
 {"model": "/models/Llama-3.2-1B-Instruct-Q8_0.gguf", "model_alias": "llama3.2-1b", "n_gpu_layers": -1, "n_ctx": 4096}]}
JSON
sed -i 's/, "chat_format": null//' /models/config.json
pgrep -f "llama_cpp.server" >/dev/null || nohup python3 -m llama_cpp.server --config_file /models/config.json > /tmp/server.log 2>&1 &
for i in $(seq 1 90); do curl -s 127.0.0.1:8000/v1/models >/dev/null && break; sleep 2; done
curl -s 127.0.0.1:8000/v1/models | python3 -c "import json,sys;print([m['id'] for m in json.load(sys.stdin)['data']])"
