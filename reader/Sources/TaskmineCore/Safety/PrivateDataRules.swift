import Foundation

/// data/rules/private_data_tables.json.
struct PrivateDataRules: Decodable, Sendable {
    struct Templates: Decodable, Sendable {
        let field: String
        let labelOnlyLine: String

        enum CodingKeys: String, CodingKey { case field, labelOnlyLine = "label_only_line" }
    }

    struct OddValues: Decodable, Sendable {
        let digits: [Int]
        let letters: [Int]

        enum CodingKeys: String, CodingKey { case digits = "digits_0_to_9", letters = "letters_A_to_Z" }
    }

    struct CheckedLabels: Decodable, Sendable {
        let taxID: [String]

        enum CodingKeys: String, CodingKey { case taxID = "TAXID" }
    }

    let separator: String
    let keylessSuffix: String
    let ibanLengths: [String: Int]
    let callingCodes: [String: String]
    let webmail: [String]
    let priority: [String: Int]
    let fieldLabels: [String: [String]]
    let checkedLabels: CheckedLabels
    let templates: Templates
    let regexes: [String: String]
    let oddValues: OddValues

    enum CodingKeys: String, CodingKey {
        case separator, keylessSuffix = "keyless_suffix", ibanLengths, callingCodes
        case webmail = "webmail_domains", priority = "priority_on_overlap", fieldLabels = "field_labels"
        case checkedLabels = "checked_field_labels", templates = "label_regex_template", regexes
        case oddValues = "codice_fiscale_odd_values"
    }
}
