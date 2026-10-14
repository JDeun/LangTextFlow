# Gemma family candidates — LangTextFlow CPU and Mac research (2026-10-14)

This is an **experiment candidate plan**, not a product integration or a measured result.
The model catalog `benchmarks/ko_en_ja_model_candidates.v1.json` is the
machine-checked source of truth. Registration never downloads model weights.

## Exact checkpoints and legal admission

| Role | Exact Hugging Face ID | Input | Model card license | Automatic download | Evidence |
|---|---|---|---|---|---|
| Lightweight prompt-based text MT | `google/gemma-3-1b-it` | Text | Gemma custom terms, HF terms acceptance required | **Blocked pending review** | https://huggingface.co/google/gemma-3-1b-it |
| Audio→translated-text and text MT feasibility | `google/gemma-4-E2B-it` | Audio, text | Apache-2.0 | Only through an explicit, budget-guarded opt-in benchmark | https://huggingface.co/google/gemma-4-E2B-it |
| Translation-specialized text MT | `google/translategemma-4b-it` | Text | Gemma custom terms, HF terms acceptance required | **Blocked pending review** | https://huggingface.co/google/translategemma-4b-it |

**Do not confuse the Gemma 4 E2B label with model residency size.**
The official card specifies 2.3B *effective* and about **5.1B total**
parameters including per-layer embeddings. Native audio is bounded at
**30 seconds per input**, and the official audio task examples cover both
original-language ASR and speech-to-translated-text. This does **not**
constitute native unbounded streaming or simultaneous bilingual audience
rendering. Run audio and text benchmark tracks separately.

For the gated Gemma 3 / TranslateGemma checkpoints, "license: gemma"
does not mean Apache-2.0, MIT, nor necessarily prohibition of commercial
deployment. It means **separate license/terms review and the operator's own
Hugging Face access acceptance** are required before downloading and before
any distribution analysis. Never automate token/credential prompts, mirror
gated weights, or bypass access controls.

## Comparison matrix (same licensed/consented evidence)

Prioritize the 6 directed paths:
`en→ko`, `ja→ko`, `ko→en`, `ko→ja`, `en→ja`, `ja→en`.

**Text-only track:** compare Gemma 3 1B / Gemma 4 E2B text /
TranslateGemma 4B against measured `facebook/m2m100_418M`
and directional OPUS only where directions exist. All candidates need their
own explicitly recorded model SHA, inference library/backend,
quantization, CPU thread count, prompt version and local hardware context.

**Native audio track (Gemma 4 E2B only):**
test a *bounded* <=30 s clip from each source language, generating an original
transcript + each requested target language. Record source transcription CER/WER,
independent target-language translation quality, prompt-injection resistance
and whether it ever copies the original source into a target caption. Compare
to the Qwen3-ASR-0.6B + M2M100 multi-stage baseline on **the same speech
recordings**. Independently prove both targets can be produced per source clip;
do not infer multi-target support from model card marketing.

**Glossary ON/OFF:** apply source-scoped operator glossary snapshots to
the real model prompt/inference path, with each trial using exactly the same
revision and sampled inputs. If no native vocabulary constraint is implemented,
mark `prompt_only` (not guaranteed), and never count an after-the-fact string
replacement as recognition support. Use the factorial contract in #139
(ASR OFF/ON x MT OFF/ON) when stage separation is possible; for direct audio
translation, label any unavailable independent stage intervention
`unsupported`, not `passed`.

**Quality gates:**
- Synthetic 12×6 smoke fixture is **unreviewed**; its chrF++ is an initial
  regression only. Later require >=20 relevant human-verified sermon terms
  per direction with independent source and target references.
- Report per-direction chrF++, term recall, name/verse citation accuracy,
  negation and numbers with human semantic review. A missing translation
  must show the explicit unavailable state, **never the source as target**.
- Report wall-clock p50/p95/p99 audio-to-caption end-to-end,
  ASR and MT separated where applicable, real-time RTF, cold/warm start,
  peak co-resident RSS/CPU and sustained 30+min queue growth.
- Linux hosted CPU is the first comparative target. Mac MLX /
  Apple Silicon and physical 4GB/8GB devices are separate *platform*
  qualification; do not transfer CPU numbers or assert iPhone readiness.
- Run the model-only experiments only through **manually dispatched,
  explicitly gated** workflows with bounded download/time/memory budgets;
  metadata CI has **no weights download**, and no unreviewed license case
  may silently opt in.
- Mark `field_ready=false` until long-audio stability and church content
  review pass. No default provider switch is authorized by catalog entry.

## Interpretation of existing evidence

The first M2M100 418M CT2 INT8 six-way reference achieved valid generated
outputs on 12 synthetic sentences per direction, but combined Qwen3-ASR
and MT had about 6.36 GB peak co-resident RSS on initial FLEURS replay.
The current Beam 4 lexical reranker did **not** improve glossary matching
(0 candidate changes) while adding latency. No Gemma benchmark numbers
have been measured by this candidate-registration PR.

## Next steps / issue ownership

- #128: full six-direction translation baseline and model comparisons.
- #126: English/Japanese missionary→Korean first-class product acceptance.
- #139: glossary A/B and human-reviewed theological correctness.
- #106/#107: physical low-memory acceptance (not inferred from "E2B").

The candidate gate can be run without model downloads:

```bash
python scripts/check_model_candidate_catalog.py --json
python -m unittest discover -s apps/server/tests -p test_model_candidate_catalog.py -v
```
