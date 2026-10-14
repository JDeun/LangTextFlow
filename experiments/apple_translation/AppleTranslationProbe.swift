// Apple Translation API macOS research bridge.
//
// This is a bounded, opt-in TEXT translation probe, not FaceTime's protected
// speech-translation pipeline. It neither copies Apple's model weights nor
// requests language downloads. Only explicitly installed language resources
// may be used. Requires macOS 26 and the matching SDK.
import Foundation
import Translation

struct TranslationInput: Decodable {
    let id: String
    let source: String
    let target: String
    let text: String
    let strategy: String
}

struct TranslationOutput: Encodable {
    let id: String
    let source: String
    let target: String
    let requestedStrategy: String
    let status: String
    let translatedText: String?
    let latencyMs: Double?
    let effectiveStrategyVerified: Bool
}

@main
struct AppleTranslationProbe {
    @MainActor
    static func main() async {
        while let line = readLine() {
            let output: TranslationOutput
            if let data = line.data(using: .utf8),
               let request = try? JSONDecoder().decode(TranslationInput.self, from: data) {
                output = await translate(request)
            } else {
                output = TranslationOutput(
                    id: "", source: "", target: "",
                    requestedStrategy: "", status: "invalid_request",
                    translatedText: nil, latencyMs: nil,
                    effectiveStrategyVerified: false
                )
            }
            let payload = (try? JSONEncoder().encode(output))
                ?? Data(#"{"status":"internal_error"}"#.utf8)
            print(String(decoding: payload, as: UTF8.self))
            fflush(stdout)
        }
    }

    @MainActor
    static func translate(_ request: TranslationInput) async -> TranslationOutput {
        func fail(_ code: String) -> TranslationOutput {
            TranslationOutput(
                id: request.id, source: request.source, target: request.target,
                requestedStrategy: request.strategy,
                status: code, translatedText: nil, latencyMs: nil,
                effectiveStrategyVerified: false
            )
        }

        let accepted = ["ko", "en", "ja"]
        guard accepted.contains(request.source), accepted.contains(request.target),
              request.source != request.target,
              request.id.utf8.count <= 128,
              !request.id.isEmpty,
              !request.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              request.text.utf8.count <= 4096,
              ["lowLatency", "highFidelity"].contains(request.strategy) else {
            return fail("invalid_request")
        }

        let source = Locale.Language(identifier: request.source)
        let target = Locale.Language(identifier: request.target)

        // Strategy APIs require the macOS 26.4 SDK; refusing highFidelity
        // on older systems prevents silently returning traditional output
        // under the label of an Apple Intelligence measurement.
        if request.strategy == "highFidelity" {
            if #available(macOS 26.4, *) {
                let availability = LanguageAvailability(preferredStrategy: .highFidelity)
                let status = await availability.status(from: source, to: target)
                guard case .installed = status else {
                    return fail("strategy_or_pair_not_installed")
                }
                let session = TranslationSession(
                    installedSource: source, target: target,
                    preferredStrategy: .highFidelity
                )
                return await measure(session, request)
            } else {
                return fail("strategy_unavailable")
            }
        }

        if #available(macOS 26.4, *) {
            let availability = LanguageAvailability(preferredStrategy: .lowLatency)
            guard case .installed = await availability.status(from: source, to: target) else {
                return fail("strategy_or_pair_not_installed")
            }
            let session = TranslationSession(
                installedSource: source, target: target,
                preferredStrategy: .lowLatency
            )
            return await measure(session, request)
        }

        let availability = LanguageAvailability()
        guard case .installed = await availability.status(from: source, to: target) else {
            return fail("strategy_or_pair_not_installed")
        }
        let session = TranslationSession(installedSource: source, target: target)
        return await measure(session, request)
    }

    @MainActor
    static func measure(
        _ session: TranslationSession, _ request: TranslationInput
    ) async -> TranslationOutput {
        let start = ProcessInfo.processInfo.systemUptime
        do {
            let response = try await session.translate(request.text)
            let elapsed = (ProcessInfo.processInfo.systemUptime - start) * 1000
            let result = response.targetText.trimmingCharacters(
                in: .whitespacesAndNewlines
            )
            let ok = !result.isEmpty && result != request.text
            // Apple can fall back from highFidelity to lowLatency. There is
            // no verified actual-backend identity here. Never claim otherwise.
            return TranslationOutput(
                id: request.id, source: request.source, target: request.target,
                requestedStrategy: request.strategy,
                status: ok ? "completed" : "unavailable",
                translatedText: ok ? result : nil,
                latencyMs: elapsed,
                effectiveStrategyVerified: false
            )
        } catch {
            // Deliberately do NOT propagate provider exceptions or private
            // filesystem paths. No source transcript in diagnostic output.
            return TranslationOutput(
                id: request.id, source: request.source, target: request.target,
                requestedStrategy: request.strategy,
                status: "translation_failed",
                translatedText: nil, latencyMs: nil,
                effectiveStrategyVerified: false
            )
        }
    }
}
