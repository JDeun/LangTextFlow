# Fun-ASR-MLT-Nano-2512: paired CPU glossary A/B (Draft)

Research-only experiment. A completed model CI run is required before any
accuracy, latency or runtime-compatibility claim.

## Why this checkpoint

The official FunAudioLLM model card says the MLT Nano is the 800M, 31-language
ASR checkpoint with Korean, English and Japanese support. The *standard* Fun-ASR
Nano checkpoint is not published as supporting Korean. The FunASR inference
interface has native hotwords and spoken-language controls. A native hotword
API may support actual ASR-stage dictionary bias; passing arguments is not
proof that words were recognized more accurately.

- https://huggingface.co/FunAudioLLM/Fun-ASR-MLT-Nano-2512
- https://github.com/QwenAudio/Fun-ASR

## Experimental procedure

1. Use an ephemeral GitHub Actions Ubuntu CPU runner with read-only
   repository permissions. Download the ~2GB checkpoint only after checking
   that its current model card still declares Apache-2.0. Record its exact
   Hugging Face revision, checkpoint hashes and Python versions.
2. Download independently licensed Fun-ASR Python inference source pinned to
   c0a20e2bb698bbad11d143bdccd6c0760e9d6e5b. Third-party Python executes
   on the ephemeral runner. Do not supply credentials.
3. Use exactly the same 9-entry, source-scoped, operator-export-compatible
   glossary snapshot as PR #135. Source-language hotwords are selected
   *before* viewing the reference transcript to avoid oracle leakage.
4. For each of three FLEURS clips in each source language KO/EN/JA, run
   the *same* model on identical WAV audio with hotwords OFF and ON.
   Keep threads, decoding options and source language fixed. Record actual
   source hypotheses, CER, inference p95, RTF, audio SHA, glossary SHA,
   reference term presence and measured resident memory.
5. Flag speech cases without glossary terms as NOT EXERCISED. An API call with
   hotwords but no matching speech cannot qualify dictionary effectiveness.

## Limits

This is an utterance-based ASR experiment, not native streaming. Three FLEURS
clips are not licensed long sermons, and the sample glossary is not the
complete, edited 157-entry church dictionary. OFF is always measured before
ON; cache/order effects can distort time comparisons. Native timestamps,
speaker diarization, real-time audio-to-translated-caption latency and
EN/JA source theological terms require separate tests and human inspection.

Stacking: PR #129 -> PR #135 -> this PR. This adds the ASR hotword stage but
does not yet combine Fun-ASR with M2M100 in a single integrated product
session. End-to-end four-way ASR/MT glossary ON/OFF is tracked in issue #139.
No production or 4/8GB hardware claim follows from a green workflow.


## First actual CPU result (2026-10-14)

[GitHub Actions run 38479147801](https://github.com/JDeun/LangTextFlow/actions/runs/38479147801)
successfully loaded the pinned Fun-ASR-MLT-Nano-2512 model and decoded **three**
real FLEURS utterances per language (KO/EN/JA), with the same immutable
9-entry source-language scoped glossary and OFF/ON prompts.

| Spoken source | CER OFF | CER ON | Inference RTF OFF | Inference RTF ON | Reference clips with glossary terms |
| --- | ---: | ---: | ---: | ---: | ---: |
| en | 0.0365 | 0.0279 | 0.5794 | 0.5994 | 0/3 |
| ja | 0.0203 | 0.0203 | 0.5981 | 0.6206 | 0/3 |
| ko | 0.0554 | 0.0792 | 0.7130 | 0.7335 | 0/3 |

**Do not claim hotword improvement.** The actual read-speech references
contained zero registered church terms, so these are background CER/RTF
observations only. Korean CER worsened on this sample. OFF was always
decoded first; order/warm-up effects remain. This first run cannot validate
the product's core sermon-specific use case, and is not a substitute for
meaningful church recordings.

Follow-up experiment code adds a **negative-control false-insertion**
measurement: any glossary term introduced into ON transcript when it was
absent in both reference and OFF transcript is reported as
`new_off_target_term_count`, never counted as success. This field is
lexical, not a human determination of hallucination or semantic intent.
Its counts require the **new** exact-head run to complete; do not project
the older run's counts onto this new field.

## Source-code A/B protocol audit (2026-10-22)

**VERIFIED-CODE:** `scripts/benchmark_funasr_mlt_glossary_cpu.py:130–215` uses the same WAV and PCM SHA for both arms, selects hotwords from the source-language scoped glossary **without** consulting the reference text, and measures CER, inference time, reference term presence, OFF/ON off-target terms, changed hypotheses and `new_off_target_term_count`. It labels a zero-reference-term run `not_exercised` and sets `glossary_field_qualification=false`.

**Design caveat:** each sample always decodes OFF **before** ON (`decode(... hotwords=[])` then `decode(... hotwords=terms)`). This is a paired comparison but **not randomized/counterbalanced**; cache/warmup/order effects can confound latency and potentially output differences. The run does not independently establish glossary effectiveness without speech containing registered terms. A lexical off-target insertion is a negative-control signal, not by itself a human-reviewed hallucination label.

**Next controlled experiment:** preregister a consented EN/JA/KO glossary-bearing audio fixture and independently checked transcript; balance OFF→ON and ON→OFF ordering (or report unavoidable bias), record per-clip term recall/precision and false insertion, and publish original artifacts plus bilingual semantic review. Keep this research-only until actual runtime ASR and MT integration is proven.
