# Experiment Conveyor — GitHub Actions model-test orchestration

## Why it exists

Model catalog checks, six-direction M2M100 CPU inference, TranslateGemma
domain quality, full audio→caption replay and macOS Apple system capability
previously ran as independent workflows without a central acceptance verdict.

The **Experiment Conveyor** runs the same established jobs via
`workflow_call`, in **dependency order**, with one opt-in entry point and
a final evidence manifest. No GitHub API dispatch tokens or long-running
idle polling job are necessary.

## Use in the Actions tab

Select **Actions → Experiment Conveyor (controlled Linux CPU and macOS)
→ Run workflow** on `main`, choose a profile:

| Profile | Trigger | Work | Cost / run time |
| --- | --- | --- | --- |
| `metadata` | Monday 03:23 UTC automatically; or manual | License status, source-language scope and safe candidate metadata on Linux | Small, no models downloaded |
| `cpu` | Manual only | metadata → real Qwen3-ASR + M2M100 CPU, 6 KO/EN/JA translation directions, short pinned FLEURS audio | Large checkpoint downloads; up to 120-minute model job |
| `mac` | Manual only | metadata → hosted macOS 26 Swift `LanguageAvailability` for 6 KO/EN/JA pairs; **no actual translation** | Mac runner only; no large model downloads |
| `full` | Manual only | metadata → M2M100 CPU → TranslateGemma text quality → audio-to-caption replay; parallel macOS 26 language-availability probe | **Expensive**, three sequential heavyweight Linux model jobs + macOS runner; each may take up to 90–120 minutes |

**Never automatically run `cpu`, `mac` or `full` on every PR or cron.**
GitHub-hosted macOS runners can have different rates and hardware from
Linux and from an M4 Mac mini. No cloud-runner scores are transferable to a
real operator's machine without a separate device benchmark.

### Fail-closed behavior

A required stage's error, cancellation or skip is not a pass. The
`report` job runs even after an upstream failure, downloads the original
run artifacts, checks expected report filenames, and publishes
`experiment-conveyor-summary.json` plus a human-readable run summary.
Missing evidence fails the experiment rather than implying it succeeded.

**Evidence semantics**:
- The metadata stage verifies catalog integrity, but never executes models.
- `m2m_cpu` executes genuine weights, but synthetic references and
  3×3 FLEURS read-speech smoke do **not** establish sermon accuracy.
- `church_quality` tests authored text, but does not prove semantic,
  negation or scripture-reference safety to a human standard.
- `church_live` replays selected audio through a real pipeline, not a
  full 30–90 minute live sermon or 4GB/8GB hardware acceptance.
- `apple` only calls `LanguageAvailability` for six directed KO/EN/JA
  pairs on a hosted macOS 26 runner. It does **not** download language
  assets, run actual Apple translation, or certify `highFidelity` or
  `lowLatency` output quality. No actual Apple model is part of any
  Linux workflow. For actual Mac translation use
  [APPLE_SYSTEM_TRANSLATION.md](APPLE_SYSTEM_TRANSLATION.md) on a Mac
  with the needed installed translation resources.
- `field_ready` stays false regardless of green Actions checks.

CPU-heavy tests share a common provenance approach: exact code commit SHA,
model and dataset revisions in their reports, true status and measured
latency/RSS. The summary stage links to the **same** run's artifacts rather
than inventing scores. Published operator text must never quietly substitute
source-language ASR as translated output.

## Out of scope

- Automated native Fun-ASR hotword experiment: research PR #140 is still
  stacked/draft and not part of the default-branch conveyor. Once independently
  merged and verified, it can be added as another reusable stage.
- NLLB-200 (CC BY-NC 4.0) or gated Gemma checkpoints: model catalog only
  until model-specific permission/terms decisions are made. Metadata check
  will never fetch their weights.
- Real iPhone system speech/translation: no generic Apple model API for
  iPhone available to a GitHub Linux runner; iOS build/signing and on-device
  runtime need a separate native host.
- Production release decision: 20+ consented and human-verified
  glossary-relevant examples per direction, ASR/MT 2×2 glossary OFF/ON,
  actual 30+ minute sermon, end-to-end p95/p99 and long-lived queue/RAM
  measurements remain tracked in #139/#106/#107.

The full profile can be manually re-run for a known commit after code
changes, but avoid running several concurrent full profiles until their
resource and GitHub Actions quota effects are understood.
