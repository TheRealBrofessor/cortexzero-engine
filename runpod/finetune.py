#!/usr/bin/env python3
"""LoRA fine-tune of a 1B router on make_router_data.py output; merge; report validation accuracy.

  finetune.py --model LiquidAI/LFM2.5-1.2B-Instruct --train d.train.jsonl --val d.val.jsonl --out out/
              [--backend unsloth|peft] [--epochs 3] [--max-steps N] [--limit-val N]

GPU: --backend unsloth (falls back to plain peft if Unsloth can't load the architecture).
CPU dry run: --backend peft --max-steps 2 (checks the whole pipeline, learns nothing).
The loss is on the assistant JSON only (prompt-completion format). Output: out/merged (HF weights, fp16)
ready for llama.cpp convert_hf_to_gguf.py, and out/metrics.json (val accuracy on exact choice).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def to_prompt_completion(rows: list[dict]) -> list[dict]:
    return [{"prompt": [r["messages"][0]], "completion": [r["messages"][1]]} for r in rows]


def choice_of(text: str) -> str | None:
    try:
        start = text.index("{")
        return json.loads(text[start: text.index("}", start) + 1]).get("choice")
    except (ValueError, AttributeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="LiquidAI/LFM2.5-1.2B-Instruct")
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--val", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--backend", choices=["unsloth", "peft"], default="unsloth")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--limit-val", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    t0 = time.time()

    import torch
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    gpu = torch.cuda.is_available()
    model = tok = None
    used = a.backend
    if a.backend == "unsloth":
        try:
            from unsloth import FastLanguageModel
            model, tok = FastLanguageModel.from_pretrained(a.model, max_seq_length=a.max_len, load_in_4bit=False,
                                                           dtype=None)
            model = FastLanguageModel.get_peft_model(
                model, r=a.rank, lora_alpha=2 * a.rank, lora_dropout=0.0, random_state=a.seed,
                target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "out_proj", "in_proj",
                                "gate_proj", "up_proj", "down_proj", "w1", "w2", "w3"])
        except Exception as e:  # architecture not supported by this Unsloth build: plain peft still works
            print(f"unsloth unavailable for {a.model} ({type(e).__name__}: {e}); falling back to peft", flush=True)
            used, model, tok = "peft", None, None
    if model is None:
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer
        tok = AutoTokenizer.from_pretrained(a.model)
        dtype = torch.bfloat16 if gpu and torch.cuda.is_bf16_supported() else (torch.float16 if gpu else torch.float32)
        model = AutoModelForCausalLM.from_pretrained(a.model, dtype=dtype, device_map="auto" if gpu else None)
        model = get_peft_model(model, LoraConfig(r=a.rank, lora_alpha=2 * a.rank, lora_dropout=0.05,
                                                 target_modules="all-linear", task_type="CAUSAL_LM"))
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    train_rows, val_rows = load_rows(a.train), load_rows(a.val)
    if a.limit_val:
        val_rows = val_rows[: a.limit_val]
    per_dev = min(a.batch, len(train_rows))
    total = a.max_steps if a.max_steps > 0 else int(a.epochs * -(-len(train_rows) // per_dev))
    cfg = SFTConfig(output_dir=str(a.out / "ckpt"), num_train_epochs=a.epochs, max_steps=a.max_steps,
                    per_device_train_batch_size=per_dev, gradient_accumulation_steps=1,
                    learning_rate=a.lr, lr_scheduler_type="cosine", warmup_steps=max(1, total // 20), logging_steps=5,
                    save_strategy="no", report_to=[], seed=a.seed, max_length=a.max_len,
                    bf16=gpu and torch.cuda.is_bf16_supported(), fp16=gpu and not torch.cuda.is_bf16_supported(),
                    completion_only_loss=True)
    trainer = SFTTrainer(model=model, args=cfg, train_dataset=Dataset.from_list(to_prompt_completion(train_rows)),
                         processing_class=tok)
    trainer.train()

    # validation: greedy decode, exact choice match (no JSON-schema constraint here, so this is a lower bound)
    model.eval()
    ok = valid = 0
    dev = next(model.parameters()).device
    for r in val_rows:
        enc = tok.apply_chat_template([r["messages"][0]], add_generation_prompt=True, return_tensors="pt",
                                      return_dict=True).to(dev)
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=24, do_sample=False, pad_token_id=tok.pad_token_id)
        got = choice_of(tok.decode(out[0][enc["input_ids"].shape[1]:], skip_special_tokens=True))
        valid += got is not None
        ok += got == r["meta"]["target"]
    metrics = {"backend": used, "gpu": gpu, "model": a.model, "train": len(train_rows), "val": len(val_rows),
               "val_acc": round(ok / max(len(val_rows), 1), 3), "val_valid_json": valid,
               "minutes": round((time.time() - t0) / 60, 1)}

    merged = model.merge_and_unload()
    merged.save_pretrained(a.out / "merged", safe_serialization=True)
    tok.save_pretrained(a.out / "merged")
    (a.out / "metrics.json").write_text(json.dumps(metrics, indent=1))
    print("METRICS", json.dumps(metrics), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
