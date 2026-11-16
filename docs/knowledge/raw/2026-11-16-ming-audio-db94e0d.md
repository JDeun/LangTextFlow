# Ming-3.0-audio-flash checkpoint provenance

- **Source URL/permalink:** https://huggingface.co/inclusionAI/Ming-3.0-audio-flash/tree/db94e0de19f6d05adc0413c255a28eb480cd1fae
- **Retrieved:** 2026-11-16
- **Revision/hash:** `db94e0de19f6d05adc0413c255a28eb480cd1fae`
- **License/rights:** Model card declares MIT. Training/benchmark/dependency rights are separate; no model weights are redistributed in this record.
- **Evidence class:** source-reported architecture/languages; retrieved repository metadata; inference not run.
- **Tracking issue:** [#468](https://github.com/JDeun/LangTextFlow/issues/468)

The official card describes a native audio-language model with nominal 126B
total / 6.5B active MoE parameters. The active count is not a complete weight
storage or RAM requirement. Mandarin/English are the primary supported-language
claims; KO/JA qualification remains an explicit experiment rather than an
inferred capability.

Retrieved HF file metadata contains 25 main model safetensors shards totaling
256,370,360,248 bytes. All safetensors, including optional talker files, total
259,329,407,384 bytes. This is a storage observation, not a measured runtime
RAM/VRAM minimum. The current approximately 10GB-RAM/56GB-free-disk Linux CPU
environment cannot store the official full checkpoint. No weights were
downloaded and no audio inference ran; no quantized candidate was selected.

Metadata endpoint: https://huggingface.co/api/models/inclusionAI/Ming-3.0-audio-flash/revision/db94e0de19f6d05adc0413c255a28eb480cd1fae?blobs=true

See the [canonical experiment](../../research/cicada-local-speech-experiments.md)
for the resource block and future controlled ASR/speech-translation comparison.
