#!/bin/bash
# On the pod (driven by finetune.sh): install the training stack from PyPI, LoRA-train each router, merge, convert to
# GGUF with the llama.cpp source uploaded from the local machine (pods can't reach GitHub), quantize Q4_K_M.
#   ft_remote.sh models.txt        (one "<hf model id> <short name>" per line)
set -uo pipefail
cd /work
# Same stack as the verified CPU dry run (transformers 5 needs torch >= 2.6; PyPI torch 2.6.0 is the cu124 build).
pip install -q "torch==2.6.0" > /work/pip.log 2>&1
pip uninstall -y -q torchvision torchaudio >> /work/pip.log 2>&1   # image builds for torch 2.4 break transformers imports
pip install -q "transformers==5.22.0" "trl==1.17.0" "peft==0.21.2" "accelerate==1.15.0" "datasets==5.1.0" \
  "huggingface_hub==2.4.0" "tokenizers==0.23.3" "safetensors" "torch==2.6.0" >> /work/pip.log 2>&1
echo "pip rc=$? $(python -c 'import torch,transformers;print(torch.__version__, transformers.__version__, torch.cuda.is_available())')"
command -v cmake >/dev/null || pip install -q cmake >> /work/pip.log 2>&1
mkdir -p llama.cpp && tar xzf llama.cpp.tar.gz -C llama.cpp --strip-components=1
pip install -q ./llama.cpp/gguf-py sentencepiece protobuf >> /work/pip.log 2>&1
( cd llama.cpp && cmake -B build -DGGML_CUDA=OFF -DLLAMA_CURL=OFF > /work/cmake.log 2>&1 \
  && cmake --build build --target llama-quantize -j > /work/build.log 2>&1 ) &
BUILD=$!
while read -r HF NAME; do
  [[ "$HF" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ && "$NAME" =~ ^[a-z0-9-]+$ ]] || { echo "skip bad line: $HF $NAME"; continue; }
  echo "== train $NAME ($HF)"
  python finetune.py --model "$HF" --train data.train.jsonl --val data.val.jsonl --out "out-$NAME" \
    --backend "${BACKEND:-peft}" < /dev/null > "train-$NAME.log" 2>&1
  echo "train rc=$?"; grep METRICS "train-$NAME.log" || tail -20 "train-$NAME.log"
done < "$1"
wait $BUILD; echo "quantize build rc=$?"
for d in out-*; do
  NAME=${d#out-}
  [ -d "$d/merged" ] || { echo "no merged model for $NAME"; continue; }
  python llama.cpp/convert_hf_to_gguf.py "$d/merged" --outtype f16 --outfile "$NAME-f16.gguf" > "convert-$NAME.log" 2>&1 \
    && ./llama.cpp/build/bin/llama-quantize "$NAME-f16.gguf" "cz-router-$NAME-Q4_K_M.gguf" Q4_K_M > "quant-$NAME.log" 2>&1
  echo "gguf $NAME rc=$? $(stat -c %s cz-router-$NAME-Q4_K_M.gguf 2>/dev/null)"
  cp "$d/metrics.json" "metrics-$NAME.json" 2>/dev/null
done
echo ALL_DONE
