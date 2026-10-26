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
