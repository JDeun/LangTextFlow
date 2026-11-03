# Real speech ASR pilot (FLEURS, manual)

For issue-by-issue real inference versus mock/not-run/rights-blocked coverage, exact source limitations and seven directly inspected Actions ZIPs, see the [2026-11-03 model evidence audit](research/model-issue-evidence-coverage-2026-11.md). Execution completion is separate from field, terminology and hardware acceptance.

This workflow supplements — it does **not replace** — field acceptance with real microphones, mixed speaker acoustics, full 90-minute events or target GPU deployment.

The workflow also performs a **minimal Korean, 3-clip, tiny-model real-speech pilot on PRs that change this benchmark implementation**. Full language/model/sample sweeps remain explicit manual runs; these PR checks execute only after an available GitHub runner starts them.

**Dataset:** Google FLEURS, test split, `ko_kr` and `en_us`, pinned revision `fdd74f7cdf01fc7641ab3072b6a38d364b3667f6`.

**License:** Creative Commons Attribution 4.0 ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).
**Credit:** Google FLEURS authors and dataset maintainers; <https://huggingface.co/datasets/google/fleurs>. Paper: Conneau et al., *FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech*, arXiv:2205.12446.

## How to run

After the manual workflow is merged into the default branch, open **Actions → Licensed FLEURS real-speech ASR (manual) → Run workflow**. Choose Korean or English, 1–8 test clips, and a CPU-compatible faster-whisper model.

The workflow installs pinned optional dataset readers and application extras, retrieves only the CC-BY dataset at the named Git revision, converts a handful of recorded clips to PCM16 16 kHz mono, and invokes **the real** `langtextflow.benchmark` ASR path per clip. It produces:
- CER/WER against supplied FLEURS human-written test references
- CPU throughput RTF and ASR queue watermark
- pinned dataset revision, reference metadata, audio SHA-256, model and environment
- downloadable per-sample JSON + summary artifacts retained for 14 days (no raw audio in artifacts)

No aggressive minimum accuracy threshold is imposed before a baseline is established. A missing transcript or failed provider **does** fail the job.

For local development, install `uv sync --locked --extra whisper --extra benchmark`, then `uv pip install "datasets==4.4.1" "soundfile==0.13.1"`, and run:

```bash
uv run --no-sync python scripts/evaluate_fleurs_asr.py \
  --language ko --samples 3 --model tiny --output artifacts/fleurs-asr
```

Note: CPU tests exercise **real recorded speech**, not mock ASR. Reading speech is not the same as noisy conferences. Real-time event pacing, acoustic echo/noise, simultaneous translation, long-form drift, VibeVoice GPU inference and user-perceived caption quality require separate evaluations. CPU RTF on tiny Whisper should not be interpreted as the target real-time 7B model throughput.

The original and target language must differ even when translation is disabled; the benchmark supplies a separate target to satisfy the runtime language contract.

## 2026-11-03 regression evidence

Source `7b6fb989037fc7a99d33e310412747a65843883b`, Korean FLEURS IDs
1883/1929/1701, the pinned revision above, CPU INT8 and 30-second decode windows:

| Model | Mean CER | Mean WER | Mean throughput RTF | Run |
|---|---:|---:|---:|---|
| tiny | 0.19593 | 0.64057 | 0.03267 | [41582860247](https://github.com/JDeun/LangTextFlow/actions/runs/41582860247) |
| base | 0.31480 | 0.54850 | 0.08673 | [41582863765](https://github.com/JDeun/LangTextFlow/actions/runs/41582863765) |

The third base clip has CER 0.6765; the mean alone hides this failure. Separate hosted
runners and model aliases do not establish a controlled speed ranking or a qualified
weight lock. These are short read-speech pilots, not live latency or field acceptance.
PCM16 SHA-256 values, respectively:

```text
6aaa41029964099abe32008ef17ec538aa71aa480cc01568f6d64a65b25b30ab
34d0749db9623cff365eeab9b9b4ec01f70975d930d9d423830547fdfe1075e9
8a9b23044c204ac5f197864b802de716dcd639d798ed270767229fb9010807e5
```

Four-second runs exposed a worker failure followed by a full-queue producer deadlock.
Merged #355 fixes the wait and records bounded failure JSON. Its reruns
[tiny 41584271837](https://github.com/JDeun/LangTextFlow/actions/runs/41584271837) and
[base 41584273456](https://github.com/JDeun/LangTextFlow/actions/runs/41584273456)
fail promptly on `0.850–4.850s` for a `4.000s` PCM input.

Merged #357 normalizes only a tail explained by valid VAD removal metadata, with a
warning; it retains rejection of nonfinite, negative, inverted and unexplained timestamps.
At implementation source `8198048f24966f2816f5d5c1beb65a9f9a96060a`,
[tiny 41585194259](https://github.com/JDeun/LangTextFlow/actions/runs/41585194259)
still fails a later unexplained `0–5s` segment. In
[base 41585202031](https://github.com/JDeun/LangTextFlow/actions/runs/41585202031),
sample 1883 completes (CER 0.1786; no duplicate/overlap counters), but sample 1929
rejects `0–22.280s` for a `4s` window. The latter partial report is not a successful
recognition result. Neither failed run supplies a valid three-clip aggregate.
The default four-second CPU path remains unqualified under #101/#158; these fixes
do not solve its recognition quality or justify a default/model promotion.

## Bounded timing recovery for live decoding

The live adapter preserves valid native decoding, with the existing four-second default,
beam size one, VAD and context hints. After the VAD normalization and common bounds check,
an invalid native timestamp triggers exactly one `without_timestamps=True` retry for
that chunk. The retry uses PCM/VAD chunk-span estimates; it is not word alignment or a
precise speech-boundary claim. The same bounds check rejects invalid retry output. Both
passes collect and validate all segments before publishing, preventing first-pass fragments
or duplicate publication. There is no new mode, retry-count setting or longer live window.

Capability `timestamp_origin="chunk_segment"` covers native segment estimates and recovered
chunk spans; word timestamps remain unsupported. Recovery logs contain only error types,
timestamp numbers and received PCM bounds, never speech text or context. Benchmark provider
JSON retains `timestamp_recovery_count` (attempts) and `timestamp_recovery_errors` (last eight
original time errors and PCM bounds), copied before engine stop. Diagnostics reset on start
and stop to separate sessions. Unexpected future nonfinite, inverted or out-of-range decoder
output still fails closed rather than being arbitrarily clamped.
The retry captures its model and session generation before native inference. Start/stop
advance that generation and reset diagnostics under a short lock; a late first pass from
an ended session cannot retry against the new model or write recovery records into it.
This fences the new retry behavior, not a claim that Python task cancellation stops all
already-running native inference.

The installed faster-whisper 1.2.1 implementation pads encoder features to 3,000 frames
even for a four-second input; timestamp-token segment ends are not bounded by the actual
PCM duration. `clip_timestamps` restricts input windows but bypasses VAD and does not bound
generated end tokens. `chunk_length=4` still pads the encoder input and did not change
small-model output in the local pilot. Neither option is used as a timing fix.

A preliminary direct-decoder small-model check returned bounded chunk spans with this
option and no text for silence. The actual adapter comparison below uses all three
canonical evaluation fixtures and their `raw_transcription` references; its measurements
supersede the preliminary one-clip quality comparison.

### Rejected global text-only pilot: tiny/base four-second reruns

Implementation source `dc4993aab957fb34676fcafb03c0e7f0749adf30` was evaluated on
2026-11-03 with the same pinned Korean IDs 1883/1929/1701 and all three PCM16
SHA-256 values listed above. Both runs completed all three samples with `status=ok`,
no provider failure or failover, and `drained_before_timeout=true`:

| Model | Mean CER | Mean WER | Mean throughput RTF | Run / retained artifact |
|---|---:|---:|---:|---|
| tiny | 1.70503 | 1.07577 | 0.23180 | [41587078736](https://github.com/JDeun/LangTextFlow/actions/runs/41587078736) / [13208187194](https://github.com/JDeun/LangTextFlow/actions/runs/41587078736/artifacts/13208187194) |
| base | 0.38797 | 0.70723 | 0.18513 | [41587086344](https://github.com/JDeun/LangTextFlow/actions/runs/41587086344) / [13208033736](https://github.com/JDeun/LangTextFlow/actions/runs/41587086344/artifacts/13208033736) |

Per-sample CER/WER/RTF, in the same ID order:

| Model | ID | CER | WER | Throughput RTF |
|---|---:|---:|---:|---:|
| tiny | 1883 | 3.6964 | 1.5000 | 0.4387 |
| tiny | 1929 | 1.0952 | 1.0000 | 0.1542 |
| tiny | 1701 | 0.3235 | 0.7273 | 0.1025 |
| base | 1883 | 0.2143 | 0.6429 | 0.1805 |
| base | 1929 | 0.7143 | 0.9333 | 0.2002 |
| base | 1701 | 0.2353 | 0.5455 | 0.1747 |

Each model emitted 8 segments (3/3/2 per sample). All saved segment times were inside
their source WAV duration; every report recorded zero exact adjacent duplicate segments
and zero timestamp-overlap candidates. Queue watermark reached its 32-chunk capacity
under maximum-speed file replay. The artifacts retain summaries and individual reports,
with a 14-day retention policy and no raw audio.

This earlier global-option proposal resolved timestamp exceptions in these six executions. It
do **not** qualify live recognition: tiny item 1883 has CER 3.6964 and base item 1929
has CER 0.7143. Zero exact duplicate counters do not rule out repeated words inside a
single segment. Model aliases are not immutable weight locks, these independent hosted
runner RTF measurements are not live latency comparisons, and three read-speech clips
do not establish noise/microphone/long-event acceptance. The four-second default and
model defaults remain unchanged; #101/#158 quality gates remain open. These pilot results
are not measurements of the narrower recovery-only implementation.

### Controlled local small-model comparison

The production-default small model was separately compared with and without the text-only
option through the actual adapter and benchmark at implementation source `dc4993a`.
The same three PCM hashes and canonical human references were used. The comparison pinned
CPU INT8, two inference threads, one worker, random seed zero per sample, beam size one,
VAD, the FLEURS evaluation title and four-second decode windows. Only the model's
`without_timestamps` argument was overridden to false for the baseline. Both arms reused
the same loaded model; their timing excludes weight loading. Weight-file SHA-256:
`3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671`;
faster-whisper 1.2.1 / CTranslate2 4.8.2 on local macOS arm64, Python 3.12.

| Mode | ID | CER | WER | Throughput RTF |
|---|---:|---:|---:|---:|
| baseline | 1883 | 0.1607 | 0.5714 | 0.6721 |
| text-only | 1883 | 0.1607 | 0.5714 | 0.6513 |
| baseline | 1929 | 0.9524 | 0.8667 | 0.7330 |
| text-only | 1929 | 1.1429 | 0.8667 | 0.8319 |
| baseline | 1701 | 0.1765 | 0.4545 | 0.6539 |
| text-only | 1701 | 0.1471 | 0.4545 | 0.7072 |
| baseline mean | — | 0.42987 | 0.63087 | 0.68633 |
| text-only mean | — | 0.48357 | 0.63087 | 0.73013 |

All six executions completed without provider failure, with all segment times inside
source duration and successful draining. Each arm emitted eight segments, with zero
exact adjacent duplicate segments and zero overlap candidates. The text-only option
regressed small-model mean CER in this pilot, particularly item 1929, while WER was
unchanged. This evidence rejected a blanket timing-mode change as a quality improvement,
leading to the narrower recovery-only scope above. The successful tiny/base executions
alone do not satisfy the source-quality gate.
These single-pass local timings are not comparable to separate hosted-runner timings.

Local review artifacts are `comparison.json`, `sources.json` and the six individual
benchmark reports in `/private/tmp/langtextflow-small-live-comparison/`; the harness is
`/private/tmp/langtextflow-compare-small-live.py`. They were deliberately kept outside
Git with the licensed temporary WAV fixtures. No new model weights were downloaded.

### Recovery-only reruns and preserved small output

Final implementation source `3c92b861177322c5f7c47434decfca3c6b84bbc6` was rerun on
the same three Korean clips, pinned dataset revision and all three PCM16 hashes above:

| Model | Mean CER | Mean WER | Mean throughput RTF | Recovery attempts | Run / artifact |
|---|---:|---:|---:|---:|---|
| tiny | 0.40347 | 0.83923 | 0.66740 | 4 | [41588873660](https://github.com/JDeun/LangTextFlow/actions/runs/41588873660) / [13208579302](https://github.com/JDeun/LangTextFlow/actions/runs/41588873660/artifacts/13208579302) |
| base | 0.31023 | 0.61517 | 0.57380 | 0 | [41588902864](https://github.com/JDeun/LangTextFlow/actions/runs/41588902864) / [13208763801](https://github.com/JDeun/LangTextFlow/actions/runs/41588902864/artifacts/13208763801) |

Per-sample CER/WER/RTF and actual recovery attempt counts:

| Model | ID | CER | WER | Throughput RTF | Retries |
|---|---:|---:|---:|---:|---:|
| tiny | 1883 | 0.3393 | 0.8571 | 0.7951 | 2 |
| tiny | 1929 | 0.5476 | 0.9333 | 1.0528 | 1 |
| tiny | 1701 | 0.3235 | 0.7273 | 0.1543 | 1 |
| base | 1883 | 0.1786 | 0.5000 | 0.1859 | 0 |
| base | 1929 | 0.4286 | 0.8000 | 1.3530 | 0 |
| base | 1701 | 0.3235 | 0.5455 | 0.1825 | 0 |

All six reports completed with successful draining, no fatal provider error or failover,
and eight segments per model inside source duration. Every duplicate/overlap counter was
zero. Tiny's artifact preserves the four original errors before recovery: `0–5s` and
`0–4.6s` on item 1883, `1.01–8.01s` on 1929, and `0–7s` on 1701, each for a four-second
PCM chunk, with input duration, start sample and rate. Base's original 22.28-second error
did not recur on this run: zero attempts are **not** evidence that that specific base
failure exercised recovery. Earlier failed artifacts above remain the failure evidence.
Automatic temperature fallback, CPU numerical variation and mutable model aliases prevent
treating independently hosted runs as a deterministic quality or speed comparison.

The final fenced implementation was also checked locally against the cached-small baseline
at `e75a8db2f16fce1cefd5e2390bb284dae8603981`, with the same controlled settings and weight
SHA above. Every one of the eight segment IDs, texts, start and end times was exactly
identical; all three recovery counts were zero. Per-item CER/WER remained
0.1607/0.5714, 0.9524/0.8667 and 0.1765/0.4545 (mean 0.42987/0.63087).
Recovery-only RTF values were 0.6681/0.7623/0.6116; the earlier baseline arm measured
0.8480/0.7571/0.6185. These one-pass local timings do not establish a speed improvement.
Raw local JSON reports are in `/private/tmp/langtextflow-small-fenced-recovery-comparison/`
and `/private/tmp/langtextflow-small-recovery-comparison/`; the final harness is
`/private/tmp/langtextflow-compare-small-fenced-recovery.py`.

The evidence supports bounded recovery of the observed tiny timestamp failures while
preserving these valid small-model outputs. It does not qualify recognition accuracy or
low-resource latency: tiny and base each exceeded RTF 1 on item 1929, and source-text errors
remain substantial. Thirty-second research results are separate measurements, not a changed
live default. Real microphone/noise, independent broader references, sustained real-time
load and long-event quality gates remain open.

### Eight-clip four-second base/small comparison (2026-11-03)

At source `900d211a0165bc334103b0bf291a61436f8e8472`, the existing manual
FLEURS workflow measured CPU INT8 base and small on eight Korean test clips:

| Model | Run | Mean CER | Mean WER | Mean throughput RTF |
|---|---|---:|---:|---:|
| base | [41632630503](https://github.com/JDeun/LangTextFlow/actions/runs/41632630503) | 0.326925 | 0.529950 | 0.224675 |
| small | [41632630199](https://github.com/JDeun/LangTextFlow/actions/runs/41632630199) | 0.395200 | 0.548625 | 0.566475 |

Original artifacts `13227939099` (base) and `13229750211` (small) were downloaded
and all reports inspected. Their ZIP SHA256 values are respectively
`9e8517835178852b30885c2b76ca9115d0d35aa7824bcefde9ee5a8ed6d075ad`
and `1cab2b14b0f0a4ddc061e0443f593a9a1f094875ab5a9d6ce7e97af4e9f33702`.
All eight ordered IDs, PCM hashes, durations, reference character counts and the
pinned dataset revision match. Reference text bytes and exact downloaded model
weight revisions are not retained by this workflow, limiting identity verification.
This is `pace=max`, 100 ms input frames and a four-second decode window; throughput
RTF and first-stable times are accelerated file replay, not live audience latency.

Base has better aggregate CER and throughput here, but the result is driven by
outliers: clip 1929 has CER 1.0000/0.9524 (base/small), and clip 2000 has
0.1731/1.3654. Small has lower CER on the other seven clips. These eight clips do
not establish general model superiority or acceptable recognition. A matched
context-window control and broader independent references remain necessary;
no live default or model recommendation changes, and `field_ready=false`.

A subsequent **30-second context control** at `e60365c35adfffe7dd76009de0e89b32f21808bc`
used the same eight ordered IDs/PCM/durations/reference lengths/dataset revision.
Git comparison confirms no ASR, workflow, evaluator or app dependency changes from
`900d211` to that source. These are separate standard CI hosts, without retained
exact CPU or downloaded-weight identity; they are not a hardware-controlled A/B.

| Model, 30s decode | Run | Mean CER | Mean WER | Mean throughput RTF |
|---|---|---:|---:|---:|
| base | [41639909697](https://github.com/JDeun/LangTextFlow/actions/runs/41639909697) | 0.2086875 | 0.4207875 | 0.0736625 |
| small | [41639934689](https://github.com/JDeun/LangTextFlow/actions/runs/41639934689) | 0.0793250 | 0.3674000 | 0.2603500 |

Downloaded original ZIPs: base artifact `13233205048`, SHA256
`0c7366553c51d1a87f24be917e1a8d6defe9d52f90852affafcc5fe9b4a6d5f5`;
small artifact `13232875752`, SHA256
`8834aac7a46b5b5eda9f4e144b373fe92a769e4867e01c54488a305c4f44a704`.
Small's clip-2000 CER changes from 1.3654 at 4s to 0.0385 at 30s; base's
clip-1929 changes from 1.0000 to 0.1429. Base clip-1701 instead worsens
0.3235→0.6765. This supports context sensitivity and reverses the aggregate
base/small accuracy ranking, rather than establishing a universal model winner.
The 30s setting covers each entire short clip here; it does not establish usable
live caption delay, post-event full-worker qualification or long-sermon quality.
Keep the four-second live default and field gates unchanged pending qualification.

## Data handling

- Avoid downloading arbitrary YouTube content. This benchmark uses an explicitly attributed dataset instead.
- No raw audio is persisted to CI artifacts or committed to Git.
- Logs and JSON reports contain example ASR transcripts, which are licensed CC-BY and should retain attribution.
- Full manual workflows and implementation-change PR pilots are bounded (sample count, audio length, model options and timeouts) to prevent unintentional heavy GPU/CPU costs.
- The FLEURS dataset download and first model bootstrap may take time. If the pinned revision is withdrawn, report failure rather than silently switching to a mutable dataset release.

## Actual CaptionRuntime ASR-to-MT smoke (#100)

The existing `scripts/evaluate_live_fleurs_translation.py` now supports real
`ollama` and `m2m100-ct2` providers through `CaptionRuntime`, its actual ASR audio
queue, per-target pipeline, persistence, and serialized hub fan-out. The separate
`compare_multilingual_m2m100_cpu.py` experiment calls ASR/MT helpers directly and
must not be presented as evidence of the queued runtime path.

Run the existing **Church live interpretation** workflow explicitly with a frozen
`source_sha`, `translation_provider=m2m100-ct2`, and `source_languages=both` for one
independent Korean clip and one independent English clip. Ordinary CI runs the
mock-wiring tests; it does not download these model weights. The workflow pins
FLEURS, faster-whisper-base, and M2M100 revisions, verifies model-card licenses,
and records PCM/model/tokenizer SHA-256 values. It uploads JSON/text/CSV evidence,
never temporary WAV/PCM, model weights, or the benchmark SQLite database.

Reports preserve source language, original ASR text/source revision, selected target
text/state, session scope, timestamp violations, first-caption/translation/commit
latency, queue/drain diagnostics, and Python RSS. The observer measures actual
`WebSocketHub.send_json` payload delivery **in process**; ASGI authorization,
network delay, browser paint, and a real microphone remain separate checks. The
Ollama variant also retains external-container resource samples.

Source CER/WER use each source clip's independent human transcription and the
server store's committed original ASR text; audience delivery loss is not graded
as recognition error. An ASR startup/inference failure leaves quality unavailable.
Different
FLEURS language IDs are not assumed to form aligned translation references:
`translation_quality.chrf` is `null` with status `unavailable`. No translations or
translation gold are fabricated. Native-model experiments are labeled
`real-inference`; unit tests that replace native weights are labeled `mock-wiring`.
A successful small smoke does not approve translation accuracy, 4GB/8GB hardware,
or 30/60/90-minute service operation. Runtime/serialization failures leave bounded
machine-readable diagnostics, and the sampler/runtime cleanup runs in `finally`.

### Controlled queued-runtime smoke (2026-11-03)

[Run 41596721553](https://github.com/JDeun/LangTextFlow/actions/runs/41596721553)
measured source `e59d2d9bbeb84a8b07b7e632e05c69fd8309bc1e` on hosted Linux CPU,
using an eight-second faster-whisper-base window and the real local M2M100 INT8
provider. ASR revision was `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`; MT revision
was `55c2e61bbf05dfb8d7abccdc3fae6fc8512fd636`. Both model cards declared MIT.
FLEURS stayed at the pinned revision above. Each direction used one independent
clip and produced two committed target captions with full draining and zero
published timestamp violations, queue drops, persistence failures, or hub failures.

| Source → target / FLEURS ID | Source CER | Source WER | First caption ms | Target p95 lag ms | Python peak RSS MiB |
|---|---:|---:|---:|---:|---:|
| EN → KO / 1904 | 0.0476 | 0.1579 | 8456.2 | 1243.3 | 3732.54 |
| KO → EN / 1883 | 0.1429 | 0.5000 | 8518.1 | 2887.6 | 3154.32 |

The Korean run recovered once after native ASR reported `0–10s` for its final
`4.48s` input chunk. That original error remains in the bounded diagnostics; the
invalid result was withheld. The published recovery timestamps were in range.
Lag uses the provider's source boundaries, including coarse recovery boundaries;
it is an adapter estimate without independently aligned word timing or browser
paint. Two segments per direction do not establish a population tail-latency SLO.

PCM16 SHA-256 values were
`fb4989f016644d6b06da77874632bb689c6ca2337698b84583ba5a6625a0de83` (EN, 10.56s)
and `6aaa41029964099abe32008ef17ec538aa71aa480cc01568f6d64a65b25b30ab` (KO, 12.48s).
[Artifact 13213000349](https://github.com/JDeun/LangTextFlow/actions/runs/41596721553/artifacts/13213000349)
contains original source/revision records, target state, model/tokenizer file hashes,
licenses, dependencies, and hardware. Its ZIP SHA-256 is
`b95fe1c667eb8e56811beb7a44caeb1696d09426cf8d076b450d367911b2c1e2`; it contains no
WAV/PCM, model weights, or SQLite files.

The later report-only correction separates server-original recognition scores from
audience delivery failures. The original records were re-evaluated under those
rules: ASR scores, requested-target coverage, and p95 values were unchanged. The
inference/backend tree, workflow, observer, and actual runtime execution body were
identical apart from completion bookkeeping. Both original fields were complete,
all queues drained, and no hub delivery was lost. Startup/disconnect/failure
boundaries are separately covered by the explicitly mocked regression tests.

Target coverage was 2/2 in each direction; translation accuracy remains unscored
with `chrF: unavailable`. The RSS values cover the Python process, including its
native model allocations, and do not qualify a complete 4GB device. Microphones,
noise, semantic review, 4GB/8GB machines, and long-event gates remain open.

### Current-source subprocess and memory checkpoint (2026-11-03)

[Run 41628932696](https://github.com/JDeun/LangTextFlow/actions/runs/41628932696)
ran the actual queued CaptionRuntime, faster-whisper-base and M2M100 INT8 child
process at source `900d211a0165bc334103b0bf291a61436f8e8472`. The original artifact's
`runner-hardware.txt` records that actual `git rev-parse HEAD`, matching its model
manifest. [Artifact 13225959685](https://github.com/JDeun/LangTextFlow/actions/runs/41628932696/artifacts/13225959685)
was independently checked from its original ZIP, SHA-256
`4840fa3c02de0abe5c1413de80dc93a2e6d7e82a1bbea50b035125dd285cef2f`.
It contains two JSON reports, model provenance, hardware and dependencies; no audio,
weights or SQLite database. Model/data revisions and licenses are unchanged from
the controlled smoke above; each direction reuses its same PCM hash and clip.

| Source → target / FLEURS ID | Source CER / WER | First caption ms | Target p95 lag ms | Paced processing RTF |
|---|---:|---:|---:|---:|
| KO → EN / 1883 (12.48s) | 0.1607 / 0.5714 | 8470.6 | 2573.8 | 1.0684 |
| EN → KO / 1904 (10.56s) | 0.0476 / 0.1579 | 8373.3 | 1098.1 | 1.0499 |

Each report contains two stable, corrected, translated and committed events: 2/2
available target captions, no unfinished source segments, full draining, and zero
published timestamp violations, queue drops/expirations, persistence failures or
hub failures/evictions. Shutdown reports ASR stopped, all queues and client counts
zero; the in-process hub observer received both commits and closed on cleanup.
ASR CER/WER were recalculated from the committed original source, not target text.
KO still changes the reference year **2011 to 2021** and retains one rejected
`0–10s` native timestamp for a `4.48s` chunk; its published recovery is explicitly
`sample_clock_estimated`. EN reports native hints, without a word-alignment claim.

| Direction | Parent RSS peak MiB | Parent + descendants RSS peak MiB | Complete / partial samples | Drain seconds |
|---|---:|---:|---:|---:|
| KO → EN | 2258.12 | 2906.33 | 9 / 0 | 0.796 |
| EN → KO | 2940.38 | 3588.45 | 8 / 0 | 0.469 |

Memory uses psutil 7.2.2, a two-second interval plus boundary samples, and a bounded
64-process traversal on hosted Linux (~15GiB reported RAM). Both trees are marked
complete with no sampler errors: samples observe parent + child, then only the
parent after cleanup. These are **sampled sums of RSS**, with shared pages counted
per process, not PSS, continuous peaks or total-device memory; external services
are excluded. Parent-only figures must not be used as complete runtime memory.

The preceding child-process [run 41624773060](https://github.com/JDeun/LangTextFlow/actions/runs/41624773060)
at `ab345776a10399e0965538c7b39d7faa4bcad2f3` has matching PCM, references, ASR
hypotheses and all ASR/MT/tokenizer file hashes in both directions (original ZIP
SHA-256 `4b96ec216c6279bcf852e22a138dada243727930cf36dec9f254716338cbc850`).
Its 2254.38/2664.95MiB readings covered only the parent; it has no process-tree
measurement and cannot establish a change in total memory. Compared with the
older `e59d2d9` smoke, the converted MT `model.bin` differs:
`4eb7e085bd0f9fb1815ebfcbecea5f7f636a22d918d44c826ddef8f61d91dae3` then versus
`a1826980fc5c037e69c7ac94fcb56c03001a66f380eb71863cc0a3879e71421b` now.
Matching upstream revisions do not make these converted weights byte-identical
or isolate code changes as the cause of differences in quality or latency.

Translation chrF remains unavailable: no independently aligned bilingual gold was
added. There is no top-level `field_ready` key; **`metrics.field_ready` is false**
in both reports and field qualification remains unapproved. One clip/two segments
per direction, paced RTF and estimated source-boundary lag do not establish
sustained throughput, a population p95, microphone/network/browser performance,
six-direction semantic accuracy, 4GB/8GB suitability or long-event readiness.

## Next phases

Independent translation-quality acceptance (#77/#108) still needs reviewed,
aligned bilingual references. Running the native runtime smoke supplies coverage
and timing evidence, not translation gold. A fake translator or copying source
text into a target track cannot be scored as a real translation result.

GPU tests with VibeVoice 7B and local translation/correction should run separately on self-hosted or paid GPU runners with logged VRAM, RTF, P95 lag and failover behavior.
