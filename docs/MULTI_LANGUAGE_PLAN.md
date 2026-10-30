# Multi-language plan

**Goal:** ship CortexZero as one fast, offline, self-contained program, while keeping this Python code as the
**reference implementation** and research bench. Each part goes in the language that suits it best, and the parts
talk only through **versioned files and JSON messages**, never by calling each other's code.

## 1. Who does what

| part | language | runs where | why this language |
|---|---|---|---|
| **Runtime engine**: safety rules, normalize, index search, router, handlers, argv policy, runner, evidence log | **Rust** | user's machine | one static binary, memory-safe where commands run, about 1 ms startup |
| **1B router inference** | **llama.cpp (C/C++)**, linked into the Rust binary | user's machine | runs the fine-tuned GGUF in-process, so no Ollama is needed; grammar-constrained output |
| **Pack authoring format** | **YAML** | authors | readable and diff-able |
| **Pack compiler + lint** | **Python** (existing `compiled.py`, `packtools.py`) | build / CI | already written and tested; it is the single source of truth for index tables |
| **Benchmark, conformance harness, charts** | **Python** (existing `bench/`, `docs/make_diagrams.py`) | dev / CI | already there; it drives *either* engine through the same CLI |
| **Router fine-tuning** | **Python + bash** (existing `runpod/`) | GPU box | the ML ecosystem is in Python |
| **Desktop / web UI** (optional, later) | **TypeScript** | user's machine | talks to `cz serve` over JSON lines; contains no engine logic |

The Python engine (`czengine/`) stays in place. New routing ideas are tried and benchmarked there first. Rust gets
a feature only after the Python version has shown it works.

## 2. The contracts (what every language agrees on)

Everything crosses language boundaries as data. Before any Rust is written, these five are frozen as JSON Schemas
in `schema/`, each with a `format` version number:

1. **Pack source** (`pack.schema.json`, `branch.schema.json`): today's YAML, documented exactly as
   `library.py` validates it.
2. **Compiled bundle** (`bundle.schema.json`): today's `cz compile` output (`format`, `sources`, `packs`, `index`),
   extended with:
   - the **normalizer word list**, so the engine no longer reads `/usr/share/dict` and behaves the same on every OS;
   - the **policy allowlist** as data (program → allowed argument forms per mode), so Python and Rust enforce the
     same table.

   The Rust engine **only reads bundles**. It never parses YAML and never builds index tables. That keeps the port
   small and makes Python the single source of truth for scoring data.
3. **Turn / evidence record** (`turn.schema.json`): today's evidence JSONL line (`question, cleaned, corrections,
   route{branch_id, decided_by, candidates}, outcome, label, answer, seconds, ts`).
4. **Router request / response** (`router.schema.json`): the prompt template, the option list, and
   `{"choice": <id | none | ask>}`. The prompt text is versioned, because the fine-tuned models depend on it.
5. **Serve protocol** (`serve.schema.json`): JSON-lines requests and responses for `cz serve` (`ask`, `suggest`,
   `consent`, `evidence`). This is what a UI or another program talks to.

## 3. Conformance harness: what makes this safe

`bench/conformance.py` (to be written in phase 1) runs **both** engines on every case set (dev 1–4, the held-out sets as scored, the red-team
set, the safety regression phrases) with `--json` output and compares them turn by turn:

| field | must match |
|---|---|
| safety rule hit, `decided_by`, `branch_id` | **exactly** |
| top-3 candidate ids, in order | exactly |
| candidate scores | within 1e-4 (both round to 4 decimals) |
| near-miss topic, other-device result | exactly |
| for checks, using the **same fake-runner fixtures**: argv sent, rules fired, final message | exactly |

It runs in CI on every change to either engine and gates the Rust release. The Rust engine replaces Python for users
only when it matches **100% on every case**, and stays that way.

Known traps to cover from day one:
- **Regex dialect.** Packs use lookahead `(?!…)` and lookbehind `(?<!…)` (9 places today), which the standard Rust
  `regex` crate does not support. Use `fancy-regex` in Rust, and add a lint rule that compiles every pack regex in
  both engines. Pack authors get one documented dialect: Python `re` minus backreferences and possessive quantifiers.
- **Ordering.** Python's `sorted` is stable. Rust must also sort stably by score descending, in pattern order, or
  tied candidates will come out in a different order.
- **Floating point.** Make both engines apply `log` and `sqrt` in the same order, then round to 4 decimals before
  comparing.
- **Unicode and lower-casing.** Define it as ASCII lower-case plus the `’` → `'` fold the index already does; test
  it with non-ASCII inputs.
- **`fmt` strings.** Only numbered fields `{1}`, `{2}` are allowed (the linter already enforces this), so Rust
  implements that subset and nothing more.

## 4. Repository layout

```
cortexzero-engine/
  czengine/        Python reference engine + tooling (unchanged location)
  engine-rs/       Rust workspace
    cz-core/         bundle loader, normalize, index search, router, safety, policy
    cz-run/          LocalRunner (argv, no shell), handlers, evidence log
    cz-llm/          llama.cpp binding (GGUF, grammar for the enum) + Ollama/OpenAI HTTP fallback
    cz-cli/          `cz` binary: ask, repl (type-ahead), serve, evidence, check-bundle
  schema/          the five JSON Schemas (§2)
  packs/           YAML sources (unchanged)
  bench/           cases, scored runs, conformance.py
  runpod/          fine-tuning (unchanged)
  ui/              (later) TypeScript client for `cz serve`
```

## 5. Phases

Each phase ends with something you can run and an exit check that code verifies. The effort figures are rough,
assuming one focused developer with AI help.

| # | phase | deliverable | exit check | effort |
|---|---|---|---|---|
| 0 | **Contracts** | `schema/` (5 schemas), the Python engine emits and validates them; the bundle gains the word list and the policy table; `cz ask --json` | Python tests and schema validation pass on every bench case | 2–3 days |
| 1 | **Harness** | `bench/conformance.py`; fake-runner fixtures saved as JSON; a regex-portability lint rule | Python vs Python = 100% (the harness checks itself) | 1–2 days |
| 2 | **Rust core**: no commands, no model | `cz-core` + `cz ask --index-only --json` reading bundles | 100% route conformance on all case sets, index-only | 1–2 weeks |
| 3 | **Rust runner + handlers** | argv policy from the bundle, LocalRunner, checks/actions with consent and verify, evidence log | 100% conformance on fixture-driven checks and actions; policy tests ported; a red-team pass on the runner | 1–2 weeks |
| 4 | **Embedded router** | `cz-llm`: llama.cpp in-process, GBNF grammar restricted to the offered ids, same prompt template | same route as the Python + Ollama run on ≥ 99% of model calls on dev4 (sampling differs slightly); **held-out v5 scored once on both** | 1 week |
| 5 | **Distribution** | static binaries for Linux x86_64/arm64 (macOS later), `cz-pack` bundles, a release workflow that runs the harness | a fresh machine runs `cz ask` with no Python installed | 3–5 days |
| 6 | **UI (optional)** | `cz serve` + a TypeScript desktop or web client | the UI only renders what serve returns; no engine logic in TypeScript | 1–2 weeks |

Rules carried over from this project:
- Score held-out sets once, and only after the Rust engine has passed conformance.
- No self-reported passes: a phase is done only when CI shows the exit check passing.
- A new pack feature lands in Python and the schema first, then in Rust in the same release.

## 6. Platform scope

Answer packs work on any OS. Check and action branches call platform programs (`nmcli`, `ip`, `resolvectl` …).
Packs therefore gain a `platforms:` field (`linux`, `macos`, `windows`). The engine hides branches that can't run on
the current OS, and routing treats them as out of library. Per-OS variants of a branch are separate branches with
the same title.

## 7. Risks

| risk | mitigation |
|---|---|
| Rust and Python drift apart | conformance gate in CI; one bundle format; Python compiles, Rust only reads |
| llama.cpp API churn | pin one llama.cpp release (as `runpod/` already does); `cz-llm` is the only crate that touches it |
| Regex differences | `fancy-regex` + a lint rule that compiles in both engines + the documented dialect |
| A fine-tuned router tied to the prompt wording | prompt template versioned in `router.schema.json`; retrain when it changes |
| Effort creeps into the UI before the engine is solid | the UI is phase 6 and optional; nothing starts there before phase 5 ships |
| Two codebases slow down research | research stays in Python only; Rust follows on a release cadence, not every experiment |

## 8. Decisions for the owner

1. Platforms for the first binary: Linux only (recommended), or Linux + macOS.
2. Embedded llama.cpp only, or keep Ollama as a supported back-end too (recommended: both, with the embedded one as
   the default).
3. Whether a UI is wanted at all (phase 6).
4. Publish the fine-tuned GGUF routers (e.g. on Hugging Face) so the binary can download them on first run.
