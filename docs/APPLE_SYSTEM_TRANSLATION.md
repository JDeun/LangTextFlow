# Apple system translation candidate for macOS/iPhone (2026-10-14)

This candidate is **not a Hugging Face model checkpoint**. Apple's private,
system-managed translation models cannot be downloaded, converted to GGUF/CT2,
copied onto Linux, or distributed with LangTextFlow. The supported integration
surface is the official **Apple Translation framework** through native
Swift/SwiftUI on Apple devices:

- [TranslationSession](https://developer.apple.com/documentation/translation/translationsession)
- [LanguageAvailability](https://developer.apple.com/documentation/translation/languageavailability)
- [lowLatency and highFidelity](https://developer.apple.com/documentation/translation/translationsession/strategy)
- [Apple's Korean/Japanese Live Translation announcement](https://www.apple.com/kr/newsroom/2025/11/chinese-japanese-and-korean-live-translation-on-airpods-and-in-phone-facetime/)

Apple's **Messages/FaceTime/Phone/AirPods Live Translation** uses Apple-built
on-device models, and officially includes Korean, English and Japanese.
**That private real-time speech pipeline is not a public general-purpose
third-party audio translation API.** The public `TranslationSession` API
accepts and returns **text**, which must be supplied by a separate ASR.
Apple's Speech framework has its own distinct device/locales gate.

The Translation framework has two translation *strategies*:

| Requested strategy | Backing technology | What a result proves |
|---|---|---|
| `lowLatency` | Traditional fast Apple system text translation; needs installed language assets | Only that native text translation ran |
| `highFidelity` | Apple Intelligence when enabled/supported, **automatically falls back** to lowLatency otherwise | The requested strategy, **not which internal model actually ran** |

High-fidelity strategy control requires the corresponding newer macOS 26.4
SDK/runtime; `AppleTranslationProbe.swift` fails explicitly on an older OS
rather than mislabeling low-latency output. Never call the result
"Apple Intelligence quality" without independent backend verification.
Use `LanguageAvailability` for **each** of the six pairs: the fact that
Phone/FaceTime support KO/EN/JA does not guarantee the public text framework
offers every pair under every strategy/device/locale.

## What is actually implemented

- `experiments/apple_translation/AppleTranslationProbe.swift`: an independent
  macOS 26+ Swift CLI speaking bounded newline-delimited JSON over stdin/out.
  Uses ONLY installed language assets. It NEVER requests permission to
  download a language or attempts to fetch/copy a model checkpoint. Refuses
  unsupported pair, missing assets, source==target, oversized input, empty
  output or source-text masquerading as translated text.
- `scripts/benchmark_apple_translation.py`: optional synthetic six-direction
  text-only benchmark against the *same* 12 KO/EN/JA fixture rows previously
  used for M2M100. Requires explicit `--allow-model-execution`; records
  latency per direction, exact input and fixture digest, requested strategy
  and explicit `field_ready=false`.
- Unit tests run anywhere with no Mac, no model and no API download.

**This PR does not integrate the Apple provider into the server or iOS app.**
The macOS executable must first compile with an appropriate Apple SDK, and
the actual API must run on a consented user's supported, configured Mac.
On iPhone the same concepts require a native Swift/iOS wrapper running on
the phone; the iPhone does not provide an external desktop HTTP service by
default. It would be incorrect to claim current Linux runners can execute
Apple's system model.

## On a compatible Mac

1. Ensure a recent Xcode Command Line Tools install and macOS 26+.
   Apple Intelligence `highFidelity` comparison additionally needs the
   newer system/SDK and Apple Intelligence enabled.
2. For low-latency translation, install the required translation languages
   yourself through **System Settings → General → Language & Region →
   Translation Languages**. There are **no implicit downloads** in the tool.
3. In your own clone, compile the research bridge:

   ```bash
   xcrun swiftc -parse-as-library      experiments/apple_translation/AppleTranslationProbe.swift      -o /tmp/ltf-apple-translation-probe
   ```

4. Run an explicitly chosen strategy and save the actual output:

   ```bash
   python scripts/benchmark_apple_translation.py      --probe-bin /tmp/ltf-apple-translation-probe      --strategy lowLatency --allow-model-execution      --output artifacts/apple-translation-low-latency.json
   ```

   For a separate `highFidelity` experiment, use the other strategy
   value on a supported device. If a pair isn't installed/available, the
   result is `not_exercised`, **not a poor translated sentence**. No
   default fallback to M2M100/source text is permitted in these runs.

## Acceptance required before product integration

- Six text directions, independent source and target settings, batch
  boundaries, cold/warm MT-only p50/p95/p99 on the same audio-derived
  **text** fixture as the M2M100 baseline. macOS 26 native API not Linux CPU.
- External ASR (Qwen3/FunASR/Speech) must separately pass actual **English
  and Japanese visiting missionary audio → Korean subtitle** tests.
- Correct handling of glossary terms, theological semantics, verses,
  names, negation and numbers. Apple Translation has **no documented
  per-call hard constrained-terminology API**; do not claim source-glossary
  guarantee from arbitrary text/prompt injection.
- 20+ human-verified relevant examples **per direction**, 30+min
  continuous CPU+ASR+MT queue/load, device RAM, cancellation and multi-target
  concurrency.
- All translated outputs must obey the LangTextFlow rule: never display
  source text under a different target-language label when translation
  is missing or failed.
- Confirm packaging/signing, system asset prerequisites, device language
  availability and privacy before enabling an **opt-in macOS provider**.
  Apple framework licensing should be reviewed before packaging and
  distribution. iOS is a separate provider client/transport project.

**Status: design and code prototype, no native Apple translation numbers
measured, not field ready.**

Related #126 (missionary directions), #139 (glossary qualification),
#128 (6-direction alternatives) and PR #146 (open Hugging Face model catalog).
