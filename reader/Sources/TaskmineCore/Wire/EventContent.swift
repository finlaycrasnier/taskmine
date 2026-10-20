import CryptoKit
import Foundation

/// The content of an event: type, hidden and tidied text, its hash, flat string metadata.
public struct EventContent: Codable, Sendable, Equatable {
    static let kinds: Set<String> = ["screen_text", "interaction", "capture_gap"]
    static let hashPattern = Rx("^[a-f0-9]{64}\\z")

    public let kind: String
    public let text: String
    public let textSHA256: String
    public let metadata: [String: String]

    /// Content whose hash is computed from `text`.
    public init(kind: String, text: String, metadata: [String: String]) {
        self.kind = kind
        self.text = text
        self.textSHA256 = Self.sha256(text)
        self.metadata = metadata
    }

    /// Lower-case hex SHA-256 of the UTF-8 text.
    public static func sha256(_ text: String) -> String {
        SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    enum CodingKeys: String, CodingKey {
        case kind = "type", text, textSHA256 = "text_sha256", metadata
    }

    /// Decodes and checks the type, hash shape and the required bundle id.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decode(String.self, forKey: .kind)
        guard Self.kinds.contains(kind) else { throw WireFormat.invalid(c, .kind, "unknown content type") }
        text = try c.decode(String.self, forKey: .text)
        textSHA256 = try c.decode(String.self, forKey: .textSHA256)
        guard Self.hashPattern.match(textSHA256 as NSString) != nil else {
            throw WireFormat.invalid(c, .textSHA256, "not 64 lower-case hex digits")
        }
        metadata = try c.decode([String: String].self, forKey: .metadata)
        guard metadata["app_bundle_id"] != nil else { throw WireFormat.invalid(c, .metadata, "app_bundle_id missing") }
    }
}
