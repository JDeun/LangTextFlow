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
