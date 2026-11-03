# M1: opt-in offline M2M100 CPU model provisioning

The production runtime never downloads models. On a network-connected
**provisioning** machine, install `huggingface_hub`, `ctranslate2`,
`transformers`, `torch` and `sentencepiece`, then run:

```bash
python scripts/provision_m2m100_ct2.py \
  --revision <PINNED_HUGGINGFACE_COMMIT_SHA> --allow-network
```

The script downloads `facebook/m2m100_418M` at the specified immutable
revision, validates KO/EN/JA tokenizer tokens, converts to CTranslate2 INT8,
and stages both cache directories. It refuses to overwrite existing caches.
Review the upstream model license and permitted redistribution before using
or shipping the resulting weights. The CLI does not redistribute any weights.

Copy `data/model-cache/m2m100-ct2` and
`data/model-cache/m2m100-tokenizer` to the offline host (or configure
`LOCAL_M2M100_CT2_DIR` and `LOCAL_M2M100_TOKENIZER_DIR`).
Install the runtime optional native dependencies on that host.
Open the operator translation provider selector and choose
`m2m100-ct2` after the capability endpoint reports configured=true.

**Acceptance not yet established:** real audio EN/JA→KO and KO→EN/JA
translation quality, memory/latency under 4GB/8GB, 30–90 minute
stability, and model license review. This provisioning path does not
declare the model field-qualified.

The live provider owns one first-party child process for native model loading
and serial inference, with at most four admitted translation calls. Loading
and each inference have a 30-second upper bound (the pipeline may impose a
shorter deadline). Timeout, cancellation and session stop close its input,
send TERM, allow one second, then KILL and reap within three seconds before
releasing process admission. Old results cannot attach to a replacement
provider. Parent-pipe EOF also ends the child during blocked native work.
An OS reap failure retains admission rather than starting more model
processes. These lifecycle guarantees do not establish model quality or
device memory/latency qualification.
