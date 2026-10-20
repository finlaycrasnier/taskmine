import Foundation

/// Hide, code, tidy and the last gate (reader spec 0, 5.1, 5.5, 5.6, 6.1).
public struct Pipeline: Sendable {
    /// Most scalars kept in a reading's text.
    public static let textLimit = 60_000
    /// Most scalars kept in a title.
    public static let titleLimit = 2_048
    /// Label-bearing metadata keys and their limits after coding.
    static let labelLimits = ["window_title": 2_048, "focus_name": 256, "focus_path": 512, "element_id": 512]

    let coder: PrivateDataCoder

    /// A pipeline that codes private data with `coder`.
    public init(coder: PrivateDataCoder) { self.coder = coder }

    // Throws when the rules exclude it or a secret remains.
    /// The reading ready to save: secrets hidden, private data coded, screen text tidied.
    public func prepare(_ reading: Reading) throws -> Reading {
        let hidden = try hide(reading)
        var out = code(hidden)
        if out.kind == "screen_text" { out.text = TextSafety.clip(Tidy.text(out.text), Self.textLimit) }
        let labels = [out.title] + Self.labelLimits.keys.sorted().compactMap { key in
            key == "window_title" && out.metadata[key] == out.title ? nil : out.metadata[key]
        }
        var counts: [String: Int] = [:]
        for part in [out.text] + labels {
            counts.merge(PrivateDataCoder.countCodes(in: part), uniquingKeysWith: +)
        }
        out.metadata["codes"] = PrivateDataCoder.countString(counts)
        try Self.lastGate(out)
        return out
    }

    /// Secrets removed from text, title and every metadata value; any exclusion throws.
    private func hide(_ reading: Reading) throws -> Reading {
        var out = reading
        out.text = try Self.hidden(reading.text, .text, Self.textLimit)
        out.title = try Self.hidden(reading.title, .title, Self.titleLimit)
        for (key, value) in reading.metadata where key != "codes" {
            let mode: TextSafety.Mode = key == "window_title" ? .title : key == "document_url" ? .url : .text
            out.metadata[key] = try Self.hidden(value, mode, nil)
        }
        out.metadata["codes"] = nil
        return out
    }

    /// One value through the secret rules.
    private static func hidden(_ value: String, _ mode: TextSafety.Mode, _ limit: Int?) throws -> String {
        let result = TextSafety.evaluate(value, mode: mode, limit: limit)
        guard let text = result.text else { throw PipelineError.excluded(reason: result.reason ?? "credential_pattern") }
        return text
    }

    /// Private data coded in text, title and label metadata; an edit value is coded under its field label.
    private func code(_ reading: Reading) -> Reading {
        var out = reading
        let field = reading.kind == "interaction" ? reading.metadata["focus_name"] : nil
        out.text = TextSafety.clip(coder.code(reading.text, field: field).text, Self.textLimit)
        out.title = TextSafety.clip(coder.code(reading.title).text, Self.titleLimit)
        for (key, limit) in Self.labelLimits {
            if let value = reading.metadata[key] { out.metadata[key] = TextSafety.clip(coder.code(value).text, limit) }
        }
        return out
    }

    /// Refuses a reading that still holds anything the secret rules would hide (spec 5.5).
    static func lastGate(_ reading: Reading) throws {
        if TextSafety.containsCredential(reading.text) || TextSafety.containsCredential(reading.title)
            || TextSafety.evaluate(reading.title, mode: .title).excluded {
            throw PipelineError.secretRemains
        }
        for (key, value) in reading.metadata {
            if TextSafety.containsCredential(key) { throw PipelineError.secretRemains }
            if key == "document_url" {
                if TextSafety.sanitizeURL(value) != value { throw PipelineError.secretRemains }
                continue
            }
            if TextSafety.containsCredential(value) { throw PipelineError.secretRemains }
            if key == "window_title" && TextSafety.evaluate(value, mode: .title).excluded {
                throw PipelineError.secretRemains
            }
        }
    }
}
