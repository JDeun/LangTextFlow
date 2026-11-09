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
not qualify Windows setup. Diagnostic run
[41656707082](https://github.com/JDeun/LangTextFlow/actions/runs/41656707082), source
`d1740b70dedfb52908dabdd8ef5d918dc4ef2da1`, completed verified downloads in about
22 seconds, then stalled in native validation until its deadline. Original
failure receipt `13241287370` has ZIP SHA-256
`fb696d4b85a5efe85e8350474eff9b2d9aeb3268667999cf0e0e06cdb63ecf94`.
It independently observed spawned PID = reported PID = 1344 and successful
cancel/reap before admission reuse; this proves direct process ownership for that
frozen worker, not which termination mechanism won. The corrected server cleanup
left no orphan entry in that job's final Actions cleanup.

Diagnostic jobs expose only fixed phases, allowlisted file keys, bounded byte
counts and fixed failure categories. The next probe separates native load return
from native destruction and cancels/reaps a still-running validation at a
60-second diagnostic bound; this does not change the product download deadline
or establish a performance qualification. A separate buffered-stdin/finalization
error-path regression was corrected with a raw FD watchdog and quiet process exit;
it is not asserted to explain the original native stall. No field quality or
physical-memory qualification follows from these packaging results.

Run [41662638133](https://github.com/JDeun/LangTextFlow/actions/runs/41662638133),
source `27240905461546fa5592ceb817945a9d06c45afb`, includes that exit fix and reached
`native_load_started` but not `native_load_returned`. The 60-second diagnostic
cancel reaped the native worker; initial cancellation also verified matching
spawned/reported PID 7428. Failure receipt `13244425774` has ZIP SHA-256
`23eb2d2f3682bec6379663acb411333e08c5f84b512975170f16f1b4b1b3c4c1`.
Thus an immediate post-return tuple destructor alone does not explain this run.
Run [41664836442](https://github.com/JDeun/LangTextFlow/actions/runs/41664836442),
source `a57fde78f101d6aa2c128562a558edfd359ddaef`, narrowed the stall to the
tokenizer class import, before tokenizer data loading or engine construction.
Its failure receipt is artifact `13246581237` (upload-reported ZIP SHA-256
`710941125f78cbb3841d9f0162bc81a33618c4e348480128c5cf78d183f38617`).

The subsequent controls use the same frozen binary per run, keep the parent pipe
open during imports, and download **no model weights**:

| Run/source | Frozen control result |
| --- | --- |
| [41669062381](https://github.com/JDeun/LangTextFlow/actions/runs/41669062381), `e84dad1738e45820d1e6372b07b5143d1cb18e2e` | No reader succeeds; blocking shared/duplicated FD readers stall, with either direct or sentencepiece-first imports. |
| [41671645044](https://github.com/JDeun/LangTextFlow/actions/runs/41671645044), `f3cb6e297fd3de13f2d06158976a4d431d93831b` | Direct blocking Windows ReadFile also stalls; it is not a successful workaround. |
| [41674053943](https://github.com/JDeun/LangTextFlow/actions/runs/41674053943), `bd52461677fa02be885c951ad06403e8184e584c` | An idle thread and PeekNamedPipe polling both import successfully; the polling reader also exits 0 on parent EOF. All nine owned children are reaped. |

The last receipt is artifact `13250527049`; its binary SHA-256 is
`01a4fbe81ae903b4a0d03d8c19c05b8c913d516c84a86d07e736f24ee3054cb4`.
These controls isolate blocking pipe reads from thread existence/import order.
The production parent-pipe helper therefore waits with PeekNamedPipe on Windows
and reads only bytes already available; POSIX keeps its raw read. Empty-but-open
pipes keep waiting, and failed handle queries stop the worker. Source regressions
also exercise native imports duplicating stdin while the watchdog is active.
Import-only diagnostics skip desktop bundling and cannot establish packaging or
actual model preparation success. Windows setup remains gated until a new
actual frozen prepare/translation/repair run passes with this change.

Run [41676133982](https://github.com/JDeun/LangTextFlow/actions/runs/41676133982),
source `0c087195ec83a577cf8dc3c09a586439d79dd6e0`, passed initial Windows
preparation: native load/unload returned, all seven resulting hashes matched,
and the earlier cancelled worker's spawned/reported PID 7728 was reaped. The
aggregate run still **failed** when its subsequent session-start HTTP request
timed out; native target translation and damaged-config repair were not observed.
Artifact `13249519838` preserves that partial result (upload-reported ZIP SHA-256
`9f7eea5d09d9799b929b35f054b4092c9956b8af8c11d31dfcf7144c9ebe2844`).
Its legacy translation worker still used a blocking stdin reader. The follow-up
applies the same shared pipe fix to legacy translation, ASR and post-event native
workers, with actual descriptor-clone, framing, RPC and EOF regressions. It does
not add the separate experimental Rust protocol. A fresh actual frozen run is
required for the resulting complete prepare → translate → repair path.

That complete Windows frozen path **passed** in
[41677258156](https://github.com/JDeun/LangTextFlow/actions/runs/41677258156), source
`fe01e0edff8b5ff51c41623e3c77ac581ff63a11`: initial preparation, actual native
translation of synthetic Korean input, and repair after deliberately damaging
the configuration all completed. Artifact `13250947434` records matching
spawned/reported PID 5212, successful cancel/reap, and all seven exact manifest
hashes. The independently checked extracted JSON SHA-256 is
`2d4c3082c7f48f9a08459f36b6d2e5668d918c8d60a97322d993f50907127fec`
(this is not the enclosing ZIP hash). Its `quality_benchmark` and
`field_qualified` are both false. The later full unsigned desktop bundle step is
separate from this successfully completed frozen-worker/API smoke.

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

## Short native compatibility evidence (2026-11-09)

The [scoped provenance record](knowledge/raw/2026-11-09-native-mt-and-dependency-compatibility.json)
links sanitized receipt companions and records both original and published-byte
hashes. Private originals remain unchanged. The runs use the same seven pinned
files above, CPU INT8/two threads and isolated frozen-lock environments on macOS
ARM64, without Torch or private audio.

| Exact source | Native dependencies | Observed lifecycle |
| --- | --- | --- |
| `0d7a5ec674771174efde45a2c518cb5bbdbe9143` ([PR #441](https://github.com/JDeun/LangTextFlow/pull/441)) | CT2 4.8.2 / SentencePiece 0.2.1 | Legacy worker and actual Rust `MtDispatcher` retain one native PID after a rejected numeric English result, then produce Japanese and a later English result; all children exit. |
| `f1db8e59bb89327afe5a5f7419becc0e2a9fde4d` ([dependency candidate #444](https://github.com/JDeun/LangTextFlow/pull/444)) | CT2 4.8.5 / SentencePiece 0.2.2 | Same files, statuses and legacy output strings; native preparation and both MT paths complete, with one native PID per path and verified child exit. |

`2010...` remains rejected for English; the Japanese result is `2010年...`.
The later owned Korean greeting produces the unchanged, unreviewed English
`Hello to.`. An available result is not a semantic-quality approval. The Rust
path uses synthetic ASR source events and the shipped native MT worker, rather
than claiming real speech recognition in that MT fixture.

The baseline runner itself exited 1 because its reporter parsed the example's
two JSONL records as one JSON object. Its original failure is retained beside
the separately labeled offline validation of saved caption/PID evidence; the
baseline example stdout was not retained. The copied candidate driver parses
JSONL correctly and exits 0. The shipped Rust example exposes its own exit code
and confirms native PID absence, but does not expose the native child's exit
status; the receipts explicitly retain that unknown value.

The dependency candidate also runs the actual Whisper inference worker with
cached base/small weights on public FLEURS clip 1883 (CC-BY-4.0): first 4 seconds
and the full 12.48 seconds succeed. First-window text, times and estimated flags
match both earlier matched-ASR arms for each model. A deliberate out-of-range
source interval still returns only `asr_worker_failed` with a null result, then
exits 1 and is reaped. The prior successful source results remain in the receipt.
These source-specific checks do not qualify later source-context changes,
Windows packaging, latency, long-duration recovery, human translation quality
or field use, and do not supersede prior negative duration evidence.
