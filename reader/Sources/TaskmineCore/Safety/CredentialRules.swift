import Foundation

/// data/rules/credential_patterns.json.
struct CredentialRules: Decodable, Sendable {
    struct Limits: Decodable, Sendable {
        let maxInput: Int
        let maxURL: Int
        let maxDecodeRounds: Int
        let maxDecodedTokens: Int
        let maxSplitValue: Int
        let genericMinDigits: Int
        let genericMinUpper: Int
        let genericMinLower: Int

        enum CodingKeys: String, CodingKey {
            case maxInput = "max_input_scalars", maxURL = "max_url_scalars"
            case maxDecodeRounds = "max_decode_rounds", maxDecodedTokens = "max_decoded_tokens"
            case maxSplitValue = "max_split_value_scalars", genericMinDigits = "generic_min_digits"
            case genericMinUpper = "generic_min_upper", genericMinLower = "generic_min_lower"
        }
    }

    struct Normalize: Decodable, Sendable {
        let invisible: String
        let space: String
        let lineBreak: String

        enum CodingKeys: String, CodingKey {
            case invisible = "invisible_class", space = "space_class", lineBreak = "line_break_class"
        }
    }

    let sanitizerVersion: Int
    let redacted: String
    let limits: Limits
    let normalize: Normalize
    let exclusion: [String: String]
    let reasons: [String: String]
    let redaction: [String: String]
    let passwordHosts: [String]

    enum CodingKeys: String, CodingKey {
        case sanitizerVersion = "sanitizer_version", redacted, limits, normalize
        case exclusion = "exclusion_patterns", reasons = "exclusion_reasons"
        case redaction = "redaction_patterns", passwordHosts = "password_manager_hosts_for_urls"
    }
}
