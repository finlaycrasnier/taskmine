import Foundation

/// data/rules/normalization_rules.json.
struct NormalizationRules: Decodable, Sendable {
    let markers: [String: String]
    let lineBreak: String
    let invisibleRun: String
    let spaceRun: String
    let webAddressFlags: String
    let webAddress: String
    let trailing: String
    let tracking: [String]
    let trackingPrefix: String
    let clockLine: String

    enum CodingKeys: String, CodingKey {
        case markers, lineBreak = "line_break_class", invisibleRun = "invisible_run", spaceRun = "space_run"
        case webAddressFlags = "web_address_flags", webAddress = "web_address"
        case trailing = "trailing_punctuation_not_part_of_address"
        case tracking = "tracking_parameters_exact", trackingPrefix = "tracking_parameter_prefix"
        case clockLine = "clock_line"
    }
}
