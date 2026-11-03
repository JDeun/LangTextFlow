# Opt-in offline M2M100 CPU model provisioning

Live inference never downloads models. The operator can explicitly prepare,
verify or repair the fixed experimental checkpoint in **Runtime & offline
models**, without selecting it or changing the default provider. The action
downloads about 0.5 GB only when the required verified bytes are absent/corrupt;
it never sends audio, transcripts or terminology. Source installs require
`uv sync --locked --extra whisper --extra local-mt`; desktop packaging includes
the same tokenizer/CT2 dependencies. Neither path installs or bundles PyTorch.
The private M2M100 child disables Torch; other providers retain their environment.

Preparation validates every file's SHA-256 and initializes the real CPU INT8
engine/tokenizer offline before publishing. It shares the native single-child
lease, has a 20-minute overall deadline and bounded HTTP/redirect/file sizes,
and supports cancel/retry. Late-spawn cancellation retains ownership until reap.
Parent EOF terminates a blocked preparation child. Only fixed HTTPS Hugging Face
distribution hosts are permitted, without inherited proxy/auth settings. Signed
redirects and native errors are never returned in job details.

The managed cache stores immutable paired generations under
`MODEL_CACHE_DIR/m2m100-native`; one atomic `current.json` selects both directories.
Failed/cancelled preparation retains the previous pointer. The repair verifies
and copies good cached files locally, fetching only failed hashes. Existing older
generations remain visible in cache inventory; clearing this area removes all of
them. Preparation and cache deletion are blocked during a session; new sessions
and native cache deletion are blocked until preparation/cleanup finishes. Each
staging job checks its complete bounded size plus configured disk reserve.
Custom `LOCAL_M2M100_*_DIR` settings preserve the manual offline contract and are
not overwritten by the UI. A malformed managed pointer fails closed until repair.

## Fixed bytes and provenance

The original checkpoint is [facebook/m2m100_418M at
55c2e61](https://huggingface.co/facebook/m2m100_418M/tree/55c2e61bbf05dfb8d7abccdc3fae6fc8512fd636),
whose pinned model card declares MIT. This remains the existing experimental
candidate, not a newly adopted model or field-quality approval. The preconverted
transport is [gn64 at
18e406c](https://huggingface.co/gn64/M2M100_418M_CTranslate2/tree/18e406c615ef2991fa74d53734bf66b0a6b10cb4).
Its metadata is **not** a trust anchor: its converted bytes must match the
independent first-party receipt below. No mirror code or Transformers remote code
is executed. The original tokenizer files come directly from the official pin.

Trust anchors, retained independently of the distribution endpoint:

- [Runtime run 41628932696](https://github.com/JDeun/LangTextFlow/actions/runs/41628932696),
  artifact `13225959685`, original ZIP `langtextflow-final-runtime-41628932696.zip`,
  SHA-256 `4840fa3c02de0abe5c1413de80dc93a2e6d7e82a1bbea50b035125dd285cef2f`.
  Source `900d211a0165bc334103b0bf291a61436f8e8472`;
  `ko/live-interpretation-report.json` and `en/live-interpretation-report.json`,
  `model_provenance.translation_files_sha256`, record all three converted files.
- [Common text run 41637393208](https://github.com/JDeun/LangTextFlow/actions/runs/41637393208),
  artifact `13230748581`, original ZIP `langtextflow-common-mt-41637393208.zip`,
  SHA-256 `0e7130598bcd0c0c0a2be80bf44f2510c8aeaeb15008825d16a2856fafddafd5`.
  Source `e60365c35adfffe7dd76009de0e89b32f21808bc`;
  `m2m100-provisioning.json`, `files_sha256.upstream/*`, records the official
  tokenizer files. Its converted `model.bin` hash is a different `4eb7…`; it is
  **not** claimed byte-identical to the selected runtime receipt. Saved/derived
  tokenizer JSON in that experiment also differs from the official input JSON.

| Installed file | SHA-256 of file bytes |
|---|---|
| model/model.bin | `a1826980fc5c037e69c7ac94fcb56c03001a66f380eb71863cc0a3879e71421b` |
| model/config.json | `8f6496adfc930cbfecbe8281112197705c488fab47d34b4829b06d7f478909af` |
| model/shared_vocabulary.json | `7eb5d0ff184c6095c7c10f9911c0aea492250abd12854f9c3d787c64b1c6397e` |
| tokenizer/tokenizer_config.json | `a53e6aa83da0b82565ed90c3849056307a9453843322ac5b8439ec4b9497fe48` |
| tokenizer/special_tokens_map.json | `c1a4f86c3874d279ae1b2a05162858db5dd6c61665d84223ed886cbcff08fda6` |
| tokenizer/sentencepiece.bpe.model | `d8f7c76ed2a5e0822be39f0a4f95a55eb19c78f4593ce609e2edbc2aea4d380a` |
| tokenizer/vocab.json | `b6e77e474aeea8f441363aca7614317c06381f3eacfe10fb9856d5081d1074cc` |

The manifest in `m2m100_prepare_worker.py` pins transport revisions, hashes and byte
limits. A transport change cannot override these hashes. First-party measured
model provenance and current preparation wiring are different evidence: see
[REAL_SPEECH_EVALUATION.md](REAL_SPEECH_EVALUATION.md) for measured scope and limits.

The explicit frozen setup run
[41650632040](https://github.com/JDeun/LangTextFlow/actions/runs/41650632040)
at source `6184947bca65c6e4c649df6b34769d0330fa3490` passed real prepare,
synthetic-source native translation, damaged-config repair and unsigned packaging
on macOS. Its small receipt artifact `13237601569` has ZIP SHA-256
`b13da27efb5cf9041208711165a2b006671d0396f8d43b0c49239685552b374f`.
The **Windows first preparation hit its deadline**; original failure artifact
`13238558967` has ZIP SHA-256
`f60c6f4411af7f0d00a30d078c31156c6728894eb394295e3fbb5e23c740951e`.
That run did not identify whether download or native validation stalled, and does
not qualify Windows setup. Subsequent diagnostic jobs expose only fixed phase,
allowlisted file keys, bounded byte counts and fixed failure categories. The
Windows-only manual gate also cancels a real frozen preparation and checks the
actual spawned/reported worker PID and reap before reusing admission. No field
quality or physical-memory qualification follows from either packaging result.

## Manual official-source conversion

On a network-connected
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
