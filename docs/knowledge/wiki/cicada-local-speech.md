# Cicada reuse and local speech candidates

Ming audio/TTS and Apple's local speech features are separate candidates with
different resource, platform and quality gates. The canonical record is
[the local speech research report](../../research/cicada-local-speech-experiments.md)
and the tracking decision is [#468](https://github.com/JDeun/LangTextFlow/issues/468).

- **Status:** investigating; manual waveform smoke and source review; no product/default promotion
- **Last reviewed:** 2026-11-16
- **Evidence class:** source metadata (`external-reported`), bounded Linux TTS execution (`reproduced`), Apple integration and field readiness (`unverified`)

## Evidence and interpretation

- [Ming TTS source/model provenance](../raw/2026-11-16-ming-tts-7247f4c.md) and
  [runtime provenance](../raw/2026-11-16-ming-tts-runtime-a39b806.md): fixed revisions
  and separate dependency rights. The [execution evidence](../../research/evidence/ming-local-speech-2026-11-16/index.json)
  retains CPU dtype failure, the earlier telemetry-related interruption, and the
  later isolated EN/KO/JA waveform smoke. Waveforms do not establish correct speech.
- [Ming audio provenance](../raw/2026-11-16-ming-audio-db94e0d.md): original checkpoint
  storage exceeds the tested host; no audio inference ran. Active parameter count
  is not a storage or resident-memory guarantee.
- [Apple platform research](../../APPLE_SYSTEM_TRANSLATION.md): existing text MT
  probe is separate from the proposed ASR helper. No Apple native execution occurred
  on this Linux host. The user's direction is supported, ready Apple local features
  first on Mac, with readiness checked per feature and language; current AUTO
  routing is not changed by the research PR.
- [Privacy correction](../../research/cicada-local-speech-experiments.md#개인정보-관련-발견과-수정):
  Python socket audit alone did not establish absence of native egress. The revised
  experiment disables ONNX telemetry before initialization and requires Linux
  kernel socket isolation. The earlier transmission payload/completion is unknown;
  the later guard probes are not a full packet audit.

## Open validation gaps

Apple helper implementation, genuine Mac execution, KO/EN/JA intelligibility,
six MT directions, bounded/cancellable integration, resource-qualified Ming audio,
and physical-device long-session acceptance remain open in #468. Paid inference
APIs and silent remote fallback are outside the requested local-first direction.

Return to the [knowledge index](index.md) and [research index](../../research/README.md).
