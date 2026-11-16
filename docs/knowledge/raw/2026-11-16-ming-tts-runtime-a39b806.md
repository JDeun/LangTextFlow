# Ming TTS reference runtime provenance

- **Source URL/permalink:** https://github.com/inclusionAI/Ming-3.0-tts/tree/a39b80660bdc5a4394d5e1d41af6dbfd9c443a54
- **Retrieved:** 2026-11-16
- **Revision/hash:** `a39b80660bdc5a4394d5e1d41af6dbfd9c443a54`
- **License/rights:** repository MIT; incorporated source headers and dependency licenses must be preserved separately. No upstream source copied into this record.
- **Evidence class:** source inspected; local smoke results are separate evidence.
- **Tracking issue:** [#468](https://github.com/JDeun/LangTextFlow/issues/468)

`ming_tts.MingTTS` accepts a local directory/device and loads BF16 weights.
A non-directory string triggers a hub download, so the LangTextFlow experiment
requires an existing absolute local directory. `campplus.onnx` loads even when
the request uses no reference recording. Text normalization and flash-attn are
optional; the experiment disables TN and uses the source's SDPA fallback.

The zero-speaker tensor is FP32 while the projection weights are BF16; several
autocast contexts are CUDA-specific. A CPU device flag alone is therefore not
evidence that every task runs on CPU. The experimental `--cpu-autocast` wrapper
is explicitly separate from unmodified runtime calls. No entire floating-point
conversion or upstream patch is silently applied.

Primary files: [pipeline](https://github.com/inclusionAI/Ming-3.0-tts/blob/a39b80660bdc5a4394d5e1d41af6dbfd9c443a54/ming_tts.py),
[generation](https://github.com/inclusionAI/Ming-3.0-tts/blob/a39b80660bdc5a4394d5e1d41af6dbfd9c443a54/modeling_bailingmm.py),
[task validation](https://github.com/inclusionAI/Ming-3.0-tts/blob/a39b80660bdc5a4394d5e1d41af6dbfd9c443a54/tts_tasks.py),
[requirements](https://github.com/inclusionAI/Ming-3.0-tts/blob/a39b80660bdc5a4394d5e1d41af6dbfd9c443a54/requirements.txt).

The README's H800/H20 test hardware is a source-reported environment, not a
minimum device requirement. The [canonical experiment](../../research/cicada-local-speech-experiments.md)
records this session's bounded CPU runs and their limits.
