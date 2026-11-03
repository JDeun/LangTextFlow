# Cached CPU MT comparison on one fixed text corpus

This evaluation CLI reuses `compare_church_cpu_pipeline.text_benchmark` unchanged.
It selects the same 16 KO→EN rows, in original order, from the 20-row
`benchmarks/church_translation_quality.v1.jsonl`, SHA256
`1b3560e9a86590e84db4d159ac2116a72ad7536e945c5a61c8f0664cc65e9688`.
Different fixture bytes are rejected. All references are synthetic and unreviewed.
There is no glossary, hotword or reference-document intervention. The product
M2M100 and TranslateGemma prompt use the same minimal church session context.

Run one candidate per command on a fixed reviewed checkout and the same CPU.
The CLI loads local files only and never provisions, converts or pulls a model.
Use already prepared CT2 INT8/tokenizer caches and a dedicated caller-owned
Ollama service without GPU access. Required evaluation dependencies are the
existing research environment's sacrebleu, psutil, CT2 and tokenizer dependencies;
this change adds no project dependency. It does not run a model automatically.

```bash
uv run --no-sync python -m scripts.compare_cached_mt_text \
  --candidate opus-small --ct2-dir /tmp/ltf-models/opus-small-ct2 \
  --tokenizer-dir /tmp/ltf-models/opus-small-tokenizer \
  --requested-revision e42d1f41b66194e6d10512f8a27bebc1f4f5097e \
  --threads 2 --timeout 30 --total-timeout 600 --output artifacts/fair-mt/opus-small.json

uv run --no-sync python -m scripts.compare_cached_mt_text \
  --candidate m2m100 --ct2-dir /tmp/ltf-models/m2m100-ct2 \
  --tokenizer-dir /tmp/ltf-models/m2m100-tokenizer \
  --requested-revision 55c2e61bbf05dfb8d7abccdc3fae6fc8512fd636 \
  --threads 2 --timeout 30 --total-timeout 600 --output artifacts/fair-mt/m2m100.json

# Set this to the exact already-installed digest from the isolated server's /api/tags.
uv run --no-sync python -m scripts.compare_cached_mt_text \
  --candidate ollama --model translategemma:4b \
  --ollama-url http://127.0.0.1:11434 --expected-digest "$LTF_EVAL_OLLAMA_DIGEST" \
  --threads 2 --timeout 30 --total-timeout 600 --output artifacts/fair-mt/translategemma.json
```

Ollama must report the exact requested tag and digest before and after scoring;
the CLI never substitutes another installed family member. It requests CPU,
two threads, temperature/seed zero and a 256-token output limit, then unloads
that benchmark model. An empty bounded generation request loads it before the
scored cases; OPUS construction and M2M100 prepare likewise load before scoring.
Unload requires `done: true`, no `error` field, and `done_reason: "unload"` when
that optional reason is returned. `ollama_unload_acknowledged` records only the
server acknowledgement; it does not claim the external Ollama OS process was
terminated/reaped. A negative/malformed acknowledgement makes cleanup fail and
the candidate ineligible. The caller still owns the dedicated service lifecycle.
Startup (including local file fingerprinting) is reported separately.
The Ollama digest is a runtime-reported **manifest/tag digest**, not a GGUF blob
hash or independently verified model revision.
OPUS/M2M100 retain beam 1 and maximum decoding length 256;
tokenization differs, so these are bounded generation settings, not equal token
budgets or proof of deterministic output. There is no text warmup: the first
translation remains in the latency population after model loading.
Async providers use one persistent event loop.

The unchanged scorer averages **sentence chrF++ over valid outputs only**;
critical-term accuracy includes all 16 cases, and latency includes every call.
Runtime exceptions/timeouts become explicit failed cases with empty invalid
outputs. Valid but semantically incorrect text remains measured text, with its
original term/chrF scores. Any failed or invalid case, incomplete run, identity
change or cleanup failure makes `comparison_eligible=false`, even if surviving
outputs have a high chrF score. Unobserved cases after an outer process timeout
are unknown; zero recorded outputs is not a claim that zero calls were attempted.

The POSIX supervisor bounds the entire candidate process group, including native
OPUS loading/inference and M2M100's product child, with TERM then KILL/reap.
OPUS's synchronous call has no separate preemptible per-case timer: its hard
bound is `--total-timeout`. Other platforms fail closed. Async preparation,
translation and cleanup have `--timeout` budgets. Reports omit paths, endpoints
and exception bodies; they retain ordered input hashes, implementation/file
hashes, requested/configured model information and runtime-reported Ollama digest.
An operator-supplied revision is labeled **requested**, while verified model and
revision remain null; hashing local files does not prove their upstream identity.

RSS covers the translation-loop process tree and excludes the external Ollama
service. It excludes startup and is not comparable as total deployment RAM
without separate Ollama/process-tree measurements. This test cannot select a
six-direction or field-optimal model, certify church semantics, or qualify
physical 4GB/8GB devices. Known-negative OPUS-big and SenseVoice are excluded.

## First common-corpus result (2026-11-03)

[Run 41637393208](https://github.com/JDeun/LangTextFlow/actions/runs/41637393208)
checked out `e60365c35adfffe7dd76009de0e89b32f21808bc`. The original ZIP,
artifact `13230748581`, SHA256
`0e7130598bcd0c0c0a2be80bf44f2510c8aeaeb15008825d16a2856fafddafd5`,
was downloaded and its three reports inspected. Ordered input hashes match for
all 16 cases. All candidates returned 16 valid outputs, recorded no execution
failures, completed cleanup and were eligible for this comparison.

| Candidate | Mean sentence chrF++ | Critical-term accuracy | Translation p50 / p95 (ms) | Startup (ms) |
|---|---:|---:|---:|---:|
| OPUS-small | 54.815 | 77.78% | 40.95 / 53.21 | 2103.08 |
| M2M100 | 48.911 | 73.33% | 310.93 / 365.54 | 2388.84 |
| TranslateGemma 4B | 57.349 | 80.00% | 7662.02 / 8678.52 | 4921.52 |

These are sequential CPU measurements on one Linux host, two configured inference
threads, glossary OFF, KO→EN text only. Filesystem cache state was uncontrolled.
OPUS-small offers the strongest speed/quality tradeoff in this small comparison;
TranslateGemma's modest lexical-score advantage costs much higher latency.
This does not select an optimal deployed combination: the references are synthetic
and unreviewed, the other five directions and co-resident live ASR were not tested,
and `field_ready` and `quality_certified` remain false. #118 stays open for its
remaining runtime and acceptance scope.
