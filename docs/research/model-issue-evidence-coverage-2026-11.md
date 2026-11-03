# Model issue evidence coverage — 2026-11-03

- **Status:** original-artifact audit; no new inference, model promotion or field acceptance
- **Repository snapshot inspected:** `a7cf808db3bbe818243082803896098ed69d5c99`
- **Method:** downloaded and parsed the seven Actions ZIPs below; compared issue histories, existing reports and harnesses. No model weights, private audio or upstream Python code were downloaded/executed for this audit.

**Real inference completed does not mean theological accuracy, six-direction live interpretation, or physical 4GB/8GB hardware qualification.** Here, real/native execution means actual model inference; it does not imply native incremental streaming. Ordinary CI, mocked translators and the Rust echo-worker protocol are separate evidence.

## Issue coverage

| Issue / model | Confirmed evidence | Remaining boundary |
| --- | --- | --- |
| [#111 SenseVoiceSmall](https://github.com/JDeun/LangTextFlow/issues/111) | Real offline Korean inference, three FLEURS clips in A | CER 0.8995 is negative evidence; converted-weight redistribution clearance and field quality are not established here |
| [#112 Qwen3-ASR 0.6B](https://github.com/JDeun/LangTextFlow/issues/112) | Real offline utterance inference in A; co-resident OPUS and multilingual M2M100 helper experiments in B/D | These adapters do not establish native streaming |
| [#113 Korean Zipformer](https://github.com/JDeun/LangTextFlow/issues/113) | Real 100ms incremental input in A; co-resident OPUS in B | Korean-only pilot; numeral errors and translation meaning remain unresolved |
| [#114 whisper.cpp base](https://github.com/JDeun/LangTextFlow/issues/114) | Real multilingual base CLI inference in A | Not an untested model. This artifact does not establish small-model, warm/cold equivalence, live MT or native streaming qualification |
| [#115 Qwen3-ASR 1.7B](https://github.com/JDeun/LangTextFlow/issues/115) | Real offline Korean inference in A | Peak process RSS 4890.059 MiB; not a complete-device RAM requirement or 4GB claim |
| [#117 TranslateGemma](https://github.com/JDeun/LangTextFlow/issues/117) | Real 20-case text inference in C; `needs-improvement` | Synthetic references require semantic review. Historical real-audio timeout evidence remains negative; C is not a fresh live-runtime pass |
| [#118 lightweight MT](https://github.com/JDeun/LangTextFlow/issues/118) | OPUS-small text and ASR+MT inference in B; OPUS-big real text inference failed its output gate | Different case counts, timing and execution paths prevent a naive ranking against TranslateGemma |
| [#121 glossary seeds](https://github.com/JDeun/LangTextFlow/issues/121) | Seed, mapping, snapshot and prompt-filtering implementation/tests | Deterministic wiring is not human-certified terminology or real-model adherence |
| [#122 CT2 glossary](https://github.com/JDeun/LangTextFlow/issues/122) | Real beam-4 lexical reranking in D, all six directions | Candidate changes **0 in every direction**; no observed glossary improvement or hard decoding guarantee |
| [#127 multilingual ASR](https://github.com/JDeun/LangTextFlow/issues/127) | Qwen utterance inference in D; actual FunASR OFF/ON KO/EN/JA inference in E | Native multilingual streaming and long sessions remain unmeasured. E has **zero reference clips containing glossary terms** in each language: `not_exercised` |
| [#128 six-direction MT](https://github.com/JDeun/LangTextFlow/issues/128) | D: 12 synthetic text cases/direction and three real clips/source with both target outputs | D composes helpers; it is not the queued CaptionRuntime. F separately measures only KO→EN and EN→KO |
| [#341 LFM3 Audio](https://github.com/JDeun/LangTextFlow/issues/341) | Pinned source/license audit only | Custom-license/entity/use/distribution scope and remote-code/CPU gates blocked; no LangTextFlow inference claimed |
| [#344 Hex](https://github.com/JDeun/LangTextFlow/issues/344) | Pinned upstream source audit; separate Rust protocol tests use echo/mock workers | No Hex native-helper ASR integration or model-performance measurement. A code license does not clear every bundled checkpoint |
| [#349 Notesoop](https://github.com/JDeun/LangTextFlow/issues/349) | Pinned source/license audit only | Dataset/AI Hub, LM and derived-artifact rights unresolved; no LangTextFlow inference claimed |

For the last three rows, reuse the [pinned upstream triage](optional-asr-upstream-triage-2026-11.md), including its immutable source records. Model-catalog metadata checks, unavailable-provider tests, fake summary LLMs and mocked audio/translator wiring do not fill those inference gaps. Additional catalog entries such as MADLAD, gated Gemma variants, mBART or restricted HY-MT must not be treated as executed from registration alone.

## A: directly inspected five-model result

[Run 41589638278](https://github.com/JDeun/LangTextFlow/actions/runs/41589638278) contains all five `MEASURED` rows and real-model JSONs. The same Korean FLEURS IDs 1883/1929/1701 and PCM hashes are recorded in `comparison.json`, at dataset revision `fdd74f7cdf01fc7641ab3072b6a38d364b3667f6` (CC BY 4.0). The hosted runner reported four AMD EPYC CPU cores and 15Gi RAM; inference used two threads.

| Model | Mean CER | Mean WER | Inference RTF | Peak process RSS MiB | Execution |
| --- | ---: | ---: | ---: | ---: | --- |
| SenseVoiceSmall INT8 | 0.8995 | 0.8950 | 0.0182 | 2161.527 | Offline utterance |
| Qwen3-ASR 0.6B INT8 | 0.0534 | 0.4023 | 0.1862 | 3520.438 | Offline utterance |
| Korean Zipformer INT8 | 0.1721 | 0.9556 | 0.0388 | 1989.277 | Native incremental |
| whisper.cpp multilingual base | 0.1203 | 0.4803 | 0.1197 | 1961.688 | Offline CLI |
| Qwen3-ASR 1.7B INT8 | 0.0554 | 0.3909 | 0.3566 | 4890.059 | Offline utterance |

These are three-clip source-ASR results, not independent sentence-meaning judgments or live MT scores. Paced wall-clock RTF is separately reported and includes deliberate waits. The whisper.cpp CLI loads within each subprocess decode; its reported `cold_load_seconds=0` measures selecting a path, **not** zero native startup time. Do not compare unlike cold-load or streaming semantics as a runtime winner.

## B–F: preserve successful execution and negative outcomes separately

- **B — OPUS, [41586664069](https://github.com/JDeun/LangTextFlow/actions/runs/41586664069):** small produced 16/16 plausibly valid outputs, synthetic mean sentence chrF++ 55.874, term accuracy 0.7556, MT p95 86.07ms. Qwen+small and Zipformer+small each produced 3/3 translations, with inference workload RTF 0.3072/0.0770 and co-resident RSS 3930.441/2724.281 MiB. This is sequential helper replay, not CaptionRuntime latency. Big produced **8/16 valid**, chrF++ 7.848, term accuracy **0**; its real-audio stage did not run after the text gate. The overall workflow failed. Neither fluent-looking output nor faster inference certifies meaning.
- **C — TranslateGemma, [41582865114](https://github.com/JDeun/LangTextFlow/actions/runs/41582865114):** 20/20 executions, synthetic mean sentence chrF++ 55.202, term accuracy 0.7818, p95 9655ms; report status **`needs-improvement`**, including missing terms and numeric/verse mismatch. Recorded Q4_K_M digest is `c49d986b0764f5881c476eb21435bb62b7abc62347aab3d4a6071e811be510a1`.
- **D — M2M100, [41589177929](https://github.com/JDeun/LangTextFlow/actions/runs/41589177929):** six text directions each have 12/12 valid outputs; all six have **zero rerank changes**. Actual Qwen source-ASR and both target outputs exist for three clips each of KO/EN/JA. Source hotword bias is explicitly not integrated. Valid coverage is not independent translation accuracy.
- **E — FunASR, [41589694247](https://github.com/JDeun/LangTextFlow/actions/runs/41589694247):** actual OFF/ON utterance inference uses checkpoint `2c088f535108689beff7e11cdc3d792af7d56552` and separately pinned implementation `c0a20e2bb698bbad11d143bdccd6c0760e9d6e5b`. Each language has three clips, zero reference-hotword clips and `glossary_test_status=not_exercised`. Observed transcript/CER differences cannot establish glossary efficacy.
- **F — queued runtime, [41596721553](https://github.com/JDeun/LangTextFlow/actions/runs/41596721553):** real faster-whisper-base + M2M100 through CaptionRuntime, one independent public clip per KO→EN / EN→KO. The original report records 2/2 committed target segments per direction and full draining. Hub JSON delivery is observed **in process**, not network or browser paint. Real-speech translation chrF remains unavailable without aligned independent references. See the [canonical runtime report](../REAL_SPEECH_EVALUATION.md#actual-captionruntime-asr-to-mt-smoke-100) for timing, source metrics and preserved timing-recovery diagnostics.

M2M100 D/F used revision `55c2e61bbf05dfb8d7abccdc3fae6fc8512fd636`; F used faster-whisper-base revision `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`. OPUS B records small/big revisions `e42d1f41b66194e6d10512f8a27bebc1f4f5097e` / `fa26583a41d95346933f26b4cb8f9b700da0d445`. These provenance records do not grant deployment approval or resolve every upstream weight/distribution obligation.

## Artifact and source provenance

All seven ZIPs were downloaded and parsed for this audit; WAV/PCM, weights and private databases are not included in this PR. The benchmark implementations predate the inspected main snapshot: **no same-current-main rerun is claimed**.

| Evidence | Original artifact | API head / frozen source SHA | Checkout evidence and limitation |
| --- | --- | --- | --- |
| A | [13210061557](https://github.com/JDeun/LangTextFlow/actions/runs/41589638278/artifacts/13210061557) | `d465f13b0cbe42f1e5ca1a2193fecd8c14b7d103` | Hardware file echoes `$GITHUB_SHA=21553aaa56846d3b9ee0d54d199b82a71b498b8c`; it does **not** run `git rev-parse HEAD`. The inspected workflow uses checkout without explicit ref. Actual job checkout-log verification is **unverified**, so head and event merge value are preserved separately |
| B small/big | [13208820219](https://github.com/JDeun/LangTextFlow/actions/runs/41586664069/artifacts/13208820219), [13208321250](https://github.com/JDeun/LangTextFlow/actions/runs/41586664069/artifacts/13208321250) | `553e0bc71c40b4d1eba3347c02e7cc1442a087f7` | Hardware file uses `git rev-parse HEAD` and records this SHA; workflow explicitly checks out PR head |
| C | [13206885497](https://github.com/JDeun/LangTextFlow/actions/runs/41582865114/artifacts/13206885497) | `7b6fb989037fc7a99d33e310412747a65843883b` | Manual run name and explicit source/GITHUB_SHA checkout expression identify this source; artifact contains no git-HEAD receipt. Independent checkout-log verification **unverified** |
| D | [13208869023](https://github.com/JDeun/LangTextFlow/actions/runs/41589177929/artifacts/13208869023) | `d8a656268b11a9c33a3a51fd4cd93a6765227b7d` | Frozen-source workflow and matching hardware environment SHA; not a separate `git rev-parse` receipt |
| E | [13209776545](https://github.com/JDeun/LangTextFlow/actions/runs/41589694247/artifacts/13209776545) | `d465f13b0cbe42f1e5ca1a2193fecd8c14b7d103` | Frozen-source workflow and matching hardware environment SHA; not a separate `git rev-parse` receipt |
| F | [13213000349](https://github.com/JDeun/LangTextFlow/actions/runs/41596721553/artifacts/13213000349) | `e59d2d9bbeb84a8b07b7e632e05c69fd8309bc1e` | Frozen source is also recorded in `models.json` and each report's model provenance; no new current-main inference |

SHA-256 of the downloaded original ZIP bytes:

```text
A        97724aea01f5953c5ad660646b4bc421ae7a417de72bc14bc92f9036ee523821
B small  4e2d9f7cde76c1c5acd8f7d6ceebe1a6f86aea912f634f97b8ee07269879fae5
B big    cf88069534647cdc1b37d1ce9d9da397f38020f2980a20c7f7effe523f27869f
C        56c215c6f9582df780c93530aca76e75d14eef075895398663701b6315c6e7f8
D        635333f6ad2a12b72842eb666c52e7a140b445541fc8910b238eb0b9512d4fda
E        f8698c1bd75c661527c3868e2c1aa335026f235831bbc1348e4ee87e6d67618c
F        b95fe1c667eb8e56811beb7a44caeb1696d09426cf8d076b450d367911b2c1e2
```

## Historical versus newly inspected evidence

The [prior-work ledger](../knowledge/wiki/prior-work-ledger.md) preserves earlier inspected reports and metrics. The initially linked five-model runs 38321459316/38322497032 are **cancelled**, not measurement evidence; A provides the current downloadable five-model result instead. Artifact listings for historical runs 38420737028, 38478446424 and 38570380566 were empty when checked. Their previously documented measurements remain historical records, **not ZIPs reverified in this audit**. In particular the historical TranslateGemma 20s real-audio timeout is not erased by C's successful text execution.

No additional model run was needed: execution evidence for the named eligible ASR/MT candidates already exists. Remaining gates concern meaningful glossary-bearing/independently aligned references, native streaming, bilingual review, cancellation/long-soak behavior and physical hardware; rights-blocked or merely registered candidates were not downloaded. Hosted four-vCPU/15Gi observations must not be promoted to donated 4GB/8GB device support.
