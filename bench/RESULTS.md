# Routing benchmark — round 1 (2026-10-29)

> Lab notebook, oldest first. Numbers are copied from bench runs; the raw once-scored runs are in `bench/scored/`.
> "Hermes" = a multi-agent LLM framework used to WRITE and blind-LABEL test messages (profiles czcasewriter,
> czlabela, czlabelb, czredteam on NVIDIA-hosted models); "eng-rN" = the working directory of that round.
> Only messages both labellers agreed on are scored; disagreements are kept in the `*-disputes.yaml` files.


Pack: home-network (29 branches) + safety. Cases: dev 55 (40 in-scope / 15 none), held-out 45 (33 / 12),
both written from the earlier persona lists before branches existed; single author → draft benchmark.
Thresholds tuned on **dev only**; held-out scored once. Models: same GGUF quantizations as local Ollama
(LFM2.5-1.2B-Instruct Q4_K_M, gemma-3-1b-it Q4_K_M, Llama-3.2-1B-Instruct Q8_0), llama.cpp on RunPod RTX A4000.

| config | dev acc | dev in-scope | dev none | held-out acc | held-out in-scope | held-out none | held-out false routes |
|---|---|---|---|---|---|---|---|
| index only | 0.945 | 40/40 | 12/15 | 0.622 | 22/33 | 6/12 | 9 |
| + LFM2.5-1.2B | 0.945 | 39/40 | 13/15 | 0.667 | 19/33 | **11/12** | **1** |
| + gemma3-1b | 0.909 | 40/40 | 10/15 | 0.689 | **29/33** | 2/12 | 14 |
| + llama3.2-1b | 0.945 | 39/40 | 13/15 | 0.689 | 20/33 | **11/12** | 2 |

Parity: dev results on the GPU pod (llama.cpp) matched local Ollama (CPU) exactly for LFM and gemma.
Latency: index-only ~2 ms; with model ~0.1–0.7 s mean (model called only when the index is unsure).
RunPod cost: $0.16.

## Why held-out dropped (index-only failures, diagnosis only — not used for tuning)
- 8 in-scope refused: everyday phrasing missing from patterns/synonyms ("kids' rooms", "froze", "choppy",
  "neighbors", "back office", "doesn't show up").
- 6 out-of-scope routed: generic words ("set up", "how do I", "disconnecting", "print") pulled unrelated branches.
- 3 wrong branch.
**The models behave differently:** LFM and llama refuse safely (11/12) but ask too often (16–17 asks);
gemma routes aggressively (29/33 in-scope) but misroutes out-of-scope (2/12). No model fixes missing coverage.

## Next (before any new held-out scoring)
1. Paraphrase expansion: 15–20 everyday phrasings per branch + synonyms (Argon/Hermes drafts, gated, dev-only).
2. Down-weight generic verbs ("set up", "how do i"); require a rare matched word per branch.
3. Let the model see more candidates when the index is low but non-zero; reduce "ask" bias in the prompt.
4. New, independent held-out set (other author: Hermes/Codex blind) — this one is now seen.
5. Fine-tune (Unsloth) the best refuser (LFM2.5 or llama3.2) on routing data.

# Round 2 (2026-10-29): agentsh + coverage + independent held-out

## Execution safety (agentsh)
Branch steps are argv-only (no shell). Every command runs through agentsh v0.20.5 under `cz-readonly` /
`cz-actions`. Live: 18/19 check branches give identical answers under agentsh vs plain local (the 19th was a
transient proxy timeout, fixed). `tests/test_agentsh_policy.py` (real sandbox): nmcli/systemctl/resolvectl
mutations, `ip link set`, `ss -K`, shells, python, rm, file writes by allowed tools (curl -o, openssl -out)
and secret reads are all denied. The test caught one real gap (ip write verbs) which was fixed.

## Independent held-out v2 (Hermes)
90 messages written by Hermes czcasewriter (nemotron-3-ultra), blind-labelled by czlabela (nemotron-3-ultra)
and czlabelb (gpt-oss-20b); 75 agreed (19 none) kept, 15 disputes in `heldout2-disputes.yaml`.
Scored ONCE with the round-1-tuned engine (+ paraphrases, synonyms, generic-verb fillers, new prompt):

| config | acc | in-scope | none | false routes | asks |
|---|---|---|---|---|---|
| index only | 0.400 | 11/56 | 19/19 | 1 | 0 |
| + LFM2.5-1.2B | 0.320 | 6/56 | 18/19 | 2 | 64 |
| + llama3.2-1b | 0.453 | 19/56 | 15/19 | 7 | 8 |
| + gemma3-1b | 0.653 | 49/56 | 0/19 | 26 | 0 |

Diagnosis: these messages are long (median 38 words) and conversational. The index RANKS well (correct
branch #1 for 43/56, in top-3 for 54/56) but absolute scores were calibrated on short questions, so
nearly everything fell into the ambiguous band; LFM then asked, gemma guessed (never refused).

## After tuning on dev + dev2 (held-out v2 is now dev2 — no honest held-out number for this config yet)
Capped query weighting (long messages no longer sink matches), ACCEPT 0.60, "ask" falls back to the index
leader when it is above ACCEPT.

| config | dev acc | dev2 acc | dev2 in-scope | dev2 none | dev2 false |
|---|---|---|---|---|---|
| index only | 0.945 | 0.693 | 43/56 | 9/19 | 21 |
| + LFM2.5-1.2B | 0.945 | 0.693 | 43/56 | 9/19 | 21 |
| + llama3.2-1b | 0.909 | 0.587 | 38/56 | 6/19 | 20 |
| + gemma3-1b | 0.836 | 0.613 | 46/56 | 0/19 | 29 |

## What this round shows
- Untuned 1B routers add little on realistic messages: LFM is cautious (asks/refuses), gemma never refuses,
  llama sits between. None reliably separates near-miss out-of-scope messages.
- The index + structure carries routing; the open problem is REFUSAL of near-misses in long messages.

## Next
1. Fine-tune the 1B router (Unsloth LoRA) on routing data: branch patterns + paraphrases + dev/dev2 +
   CLINC150-style out-of-scope, with near-miss negatives. Needs a GPU (RunPod is set up, not started).
2. Per-branch "core concept" requirement (a branch can only win if one of its defining terms appears).
3. New Hermes held-out v3, scored once, for the tuned config.

> 2026-10-30: agentsh (a third-party sandbox) was removed from the engine; it is not shipped. The agentsh results above are historical (that test file no longer exists); the engine now uses the plain no-shell LocalRunner plus its own argv allowlist (`czengine/policy.py`).

# Round 3 (2026-10-30, P1): core concepts + near-miss decoys + inflection stems
Changes (all tuned on dev + dev2 only):
- `core:` per branch (AND of OR-groups, synonyms count): a branch is not a candidate unless its concept is in the message.
- `packs/home-network/near-misses.yaml`: 17 out-of-scope look-alike topics (woodworking router, ship port, account
  passwords, phone/console wifi, printer ink, bluetooth, casting, smart home, buying hardware, ISP billing, QoS, ...).
  If a look-alike scores >= the best branch, route = none (`decided_by: near-miss`).
- Inflection stems (flushing -> flush, renewing -> renew) mapped onto library words.
- Branch-vocabulary coverage tie-breaker (COVER_BONUS 0.15, scaled by 1-score so it never closes a gap at the top).
- New patterns for 7 branches written from dev2 misses.

| config | dev acc | dev none | dev false | dev2 acc | dev2 in-scope | dev2 none | dev2 false |
|---|---|---|---|---|---|---|---|
| index only | 1.000 | 15/15 | 0 | 0.987 | 55/56 | 19/19 | 1 |
| + LFM2.5-1.2B | 1.000 | 15/15 | 0 | 0.987 | 55/56 | 19/19 | 1 |
| + llama3.2-1b | 0.982 | 15/15 | 0 | 0.800 | 41/56 | 19/19 | 1 |
| + gemma3-1b | 0.982 | 14/15 | 1 | 0.867 | 48/56 | 17/19 | 10 |

dev+dev2 index-only: acc 129/130 = 0.992, false routes 1/130 = 0.8%, correct refusal 34/34. Targets met ON DEV —
these are tuning-set numbers and overfit is expected; the honest number is held-out v3 (P2), scored once.
Untuned llama/gemma still make routing worse when consulted; LFM mostly defers to the index.

## Independent held-out v3 (Hermes, eng-r2) — scored ONCE, 2026-10-30
120 messages from Hermes czcasewriter (nemotron-3-ultra) in three length batches (40 long 38-52 words, 50 medium,
30 short sampled from a first all-short attempt), blind-labelled by czlabela (nemotron-3-ultra) and czlabelb
(gpt-oss-20b); 98 agreed (23 none) → `cases-heldout3.yaml`, 22 disputes → `heldout3-disputes.yaml`.
Config = Round 3 (P1) engine, packs home-network + safety. Nothing was tuned on this file before this run.

| config | acc | in-scope | none | false routes | asks | model calls |
|---|---|---|---|---|---|---|
| index only | 0.816 | 63/75 | 17/23 | 12 | 0 | 0 |
| + LFM2.5-1.2B | 0.816 | 63/75 | 17/23 | 12 | 3 | 6 |
| + llama3.2-1b | 0.684 | 50/75 | 17/23 | 11 | 1 | 22 |
| + gemma3-1b | 0.847 | 66/75 | 17/23 | 12 | 0 | 23 |
| index only, all 4 packs loaded | 0.816 | 63/75 | 17/23 | 13 | 0 | 0 |

Honest reading: index-only went from 0.400 (held-out v2, old config) to 0.816 on a new independent set, but false
routes are 12/98 (12%) and correct refusal 17/23 (0.74): the P1 targets (≤5%, ≥0.85) were met on dev only.
gemma3-1b helps in-scope here (+3) without extra false routes; llama hurts. held-out v3 is now dev3.

## family-camping held-out v1 (Hermes eng-r5-camp) — scored ONCE, 2026-10-30
60 messages (czcasewriter), blind labels czlabela + czlabelb, 54 agreed (11 none). Packs: family-camping + safety.
Before this the pack had only its author's own dev set (36/36 — optimistic, as noted).

| config | acc | in-scope | none | false |
|---|---|---|---|---|
| index only | 0.556 | 20/43 | 10/11 | 1 |
| + LFM2.5-1.2B | 0.556 | 20/43 | 10/11 | 1 (22 asks) |
| + llama3.2-1b | 0.611 | 24/43 | 9/11 | 3 |
| + gemma3-1b | 0.685 | 32/43 | 5/11 | 9 |
| index only, all packs | 0.574 | 22/43 | 9/11 | 2 |

Honest reading: a new 14-branch pack with ~8 patterns per branch refuses safely but misses half of real phrasings.
It is now camping dev2 (`packs/family-camping/cases/dev2.yaml`).

# State after P6 fixes (2026-10-30) — tuning-set numbers only (index-only)
| set | home-network+safety | all 4 packs |
|---|---|---|
| dev (55) | 1.000 | 1.000 |
| dev2 (75) | 0.973 | 0.960 |
| dev3 (98, was held-out v3) | 0.969 | 0.990 |
| camping dev (36) / dev2 (54, was camping held-out) | — | 1.000 / 0.981 (camping+safety) |
Red-team (Hermes czredteam, 80 attacks): out-of-scope "other device" misroutes refused 27/35 (was 14/35).
Next honest number: held-out v4 (`cases-heldout4.yaml`, 97 agreed, only 12 none) — built, NOT scored yet;
reserved for the fine-tuned router (P3), scored once together with index-only and the untuned 1Bs.

# Round 4 (2026-10-30, P3): fine-tuned 1B routers — held-out v4 scored ONCE
Training (RunPod RTX A4000 secure, ~75 min pod time incl. slow installs, $0.31; first attempt on a community 3090 got
no SSH port, terminated after 16 min, $0.06): LoRA r16, 3 epochs, peft on torch 2.6 / transformers 5.22 / trl 1.17,
910 examples (dev + dev2 + dev3 + 167 agreed Hermes training messages + branch patterns + near-misses; no held-out,
no private data). Merged → GGUF f16 → Q4_K_M (llama.cpp v0.8.0) → Ollama `cz-router-lfm`, `cz-router-llama`.
In-training validation (same distribution): LFM 0.912, llama 0.934.

Held-out v4: 119 Hermes messages (eng-r4: 40 long, 50 medium, 30 short), blind labels czlabela + czlabelb,
97 agreed (only 12 none) → `cases-heldout4.yaml`. Nothing was tuned on it. Engine = post-P6 config.

| config | acc | in-scope | none | false routes | model calls |
|---|---|---|---|---|---|
| index only | 0.773 | 66/85 | 9/12 | 14 | 0 |
| + LFM2.5-1.2B (untuned) | 0.804 | 69/85 | 9/12 | 11 | 10 |
| + llama3.2-1b (untuned) | 0.660 | 54/85 | 10/12 | 10 | 27 |
| + gemma3-1b (untuned) | 0.876 | 76/85 | 9/12 | 9 | 30 |
| **+ cz-router-lfm (fine-tuned)** | **0.876** | 75/85 | 10/12 | 8 | 31 |
| **+ cz-router-llama (fine-tuned)** | **0.897** | 77/85 | 10/12 | 7 | 31 |

Honest reading: fine-tuning turned the 1B router from neutral/harmful into the best component (llama 0.660 → 0.897,
LFM 0.804 → 0.876) and cut false routes 14 → 7 vs index-only. Still 7/97 (7%) false routes; v4 has few
out-of-scope messages (12), so the refusal side is weakly measured. Dev sets after tuning (training data for the
routers — not honest): dev 1.000/1.000, dev2 .973/1.000, dev3 .990/.990 (lfm/llama).
held-out v4 is now dev4. Recommended config: index + cz-router-llama.
