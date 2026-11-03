# Real speech ASR pilot (FLEURS, manual)

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

## Data handling

- Avoid downloading arbitrary YouTube content. This benchmark uses an explicitly attributed dataset instead.
- No raw audio is persisted to CI artifacts or committed to Git.
- Logs and JSON reports contain example ASR transcripts, which are licensed CC-BY and should retain attribution.
- Full manual workflows and implementation-change PR pilots are bounded (sample count, audio length, model options and timeouts) to prevent unintentional heavy GPU/CPU costs.
- The FLEURS dataset download and first model bootstrap may take time. If the pinned revision is withdrawn, report failure rather than silently switching to a mutable dataset release.

## Next phases

Issue #77 also tracks a **real translation** benchmark. It needs independently aligned bilingual references and a running target translation model. A fake translator or copying original text into the target language cannot be scored as a real translation result.

GPU tests with VibeVoice 7B and local translation/correction should run separately on self-hosted or paid GPU runners with logged VRAM, RTF, P95 lag and failover behavior.
