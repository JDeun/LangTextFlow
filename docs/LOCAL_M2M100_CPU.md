# Experimental local CPU M2M100 translator

Status: **adapter implementation only; not field qualified**. Implementation #157; program architecture #154. The actual live application now recognizes the opt-in `m2m100-ct2` translator, but the checkpoint provisioner and real-audio quality gate are still required. A green mock test is not model-quality evidence.

## Source/target scope

The provider accepts exactly the six ordered distinct pairs among `ko`, `en`, and `ja`. EN/JA missionary speech to KO captions is supported at the *routing/API-contract* level. This is not evidence that the model's translation is accurate enough for theological sermons.

## Local-only setup contract

The translator is **offline-only**, never automatically opens a Hugging Face or external service URL, and fails closed when artifacts are missing. Configure the Python server using `LANGTEXTFLOW_LOCAL_M2M100_CT2_DIR` and `LANGTEXTFLOW_LOCAL_M2M100_TOKENIZER_DIR` (defaults `data/model-cache/m2m100-ct2` and `data/model-cache/m2m100-tokenizer`). `LANGTEXTFLOW_LOCAL_M2M100_CPU_THREADS` controls the CPU budget.

An operator/developer may provision a **revision-pinned** `facebook/m2m100_418M` source tokenizer checkpoint to the tokenizer directory and run the CTranslate2 converter with CPU INT8 to generate the model directory. The existing reproducible research workflow [multilingual-cpu-m2m100.yml](../.github/workflows/multilingual-cpu-m2m100.yml) shows the pinned library commands and revision/hash capture. Optional runtime libraries `ctranslate2`, `transformers`, `sentencepiece` are not bundled with the minimal desktop/HTTP installation; provision them in the same Python environment and verify their licenses. **Do not ship model weights or assume a permitted deployment license without checking the exact revision.** A first-party model download/setup UI is not implemented in this PR.

In the Operator translation provider dropdown select **M2M100 CT2 CPU (experimental; local cache)**. The system preflight reports missing artifacts/dependencies; the session setup reports unavailable rather than silently falling back to another provider. Glossary reranking is **not** yet wired to this live adapter and should be considered unsupported pending #122/#139.

## Runtime and cancellation caveats

- A shared mutable M2M100 tokenizer is protected by a Python lock across inference calls; language identifiers and prefix decoding follow the six-direction research contract.
- CPU inference uses a process-scoped single-worker ThreadPoolExecutor and an explicit maximum of four admitted jobs (one running, at most three queued). A fifth admission fails closed with an unavailable translation while the source caption remains visible.
- **Canceling an asyncio waiter does not forcibly stop the underlying CTranslate2/native worker.** The native Future is shielded and retains its job-capacity slot and model/tokenizer references until completion; closing an old session cannot silently free still-running work. This prevents unbounded native CPU inference accumulation across session restarts. Forced worker termination/process isolation remains a later #157 acceptance item.
- The adapter rejects empty/untranslated-looking/structured-envelope output and unexpected output language prefix; these are formatting gates, **not** semantic accuracy or hallucination detection.
- Existing runtime correction and translation postprocessing budgets still apply; current per-segment scheduling PR stack #162→#165→#167 is independent and must be merged/tested before claiming sustained real-time multi-target operation.

## Exit gates still open

- Real-model six-direction run **inside the live FastAPI pipeline**, not merely the standalone benchmark script; save revision/hash, conversion files, sample-level outputs, Linux CPU timings and resource counters.
- Independently verified sermon/lecture source and bilingual reference quality, especially negation, scripture references and theological terminology.
- Long continuous real speech and physical 8GB / 4GB CPU availability; unverified until #106/#107/#126/#128/#139 report evidence.
- Versioned capability registry and visible UI download/provisioning need follow-on work under #157.
