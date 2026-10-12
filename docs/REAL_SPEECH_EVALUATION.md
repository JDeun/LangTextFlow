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

The benchmark language-contract fix in PR #84 is required: original and target language cannot be identical even when translation is disabled. This PR is stacked on that fix and must be rebased after #84 merges.

## Data handling

- Avoid downloading arbitrary YouTube content. This benchmark uses an explicitly attributed dataset instead.
- No raw audio is persisted to CI artifacts or committed to Git.
- Logs and JSON reports contain example ASR transcripts, which are licensed CC-BY and should retain attribution.
- Full manual workflows and implementation-change PR pilots are bounded (sample count, audio length, model options and timeouts) to prevent unintentional heavy GPU/CPU costs.
- The FLEURS dataset download and first model bootstrap may take time. If the pinned revision is withdrawn, report failure rather than silently switching to a mutable dataset release.

## Next phases

Issue #77 also tracks a **real translation** benchmark. It needs independently aligned bilingual references and a running target translation model. A fake translator or copying original text into the target language cannot be scored as a real translation result.

GPU tests with VibeVoice 7B and local translation/correction should run separately on self-hosted or paid GPU runners with logged VRAM, RTF, P95 lag and failover behavior.
