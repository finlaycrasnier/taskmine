import Foundation

/// The compiled patterns of private_data_tables.json, plus the label patterns built from its lists.
struct PrivateDataPatterns: Sendable {
    /// One label pattern: the kind it codes and whether only checked values count.
    struct Label: Sendable {
        let kind: String
        let checked: Bool
        let rx: Rx
    }

    static let shared = PrivateDataPatterns(Rules.privateData)

    let regex: [String: Rx]
    let fields: [Label]
    let labelOnly: [Label]
    let ibanBodies: [Int: Rx]
    let ibanHead = Rx("(?<![A-Za-z0-9_])[A-Za-z]{2}[0-9]{2}")
    let cardHead = Rx("(?<![A-Za-z0-9_.,+\\-/])(?=[0-9])")
    let cardDigits = Rx("[0-9](?:[ \\-]?[0-9]){0,18}")

    /// Compiles every pattern the coder uses.
    init(_ rules: PrivateDataRules) {
        regex = rules.regexes.mapValues { Rx($0) }
        fields = Self.labels(rules, template: rules.templates.field)
        labelOnly = Self.labels(rules, template: rules.templates.labelOnlyLine)
        var bodies: [Int: Rx] = [:]
        for size in Set(rules.ibanLengths.values) {
            bodies[size] = Rx("[A-Za-z0-9](?:[ \\-]?[A-Za-z0-9]){\(size - 1)}(?![A-Za-z0-9_])")
        }
        ibanBodies = bodies
    }

    /// A rule regex by key.
    subscript(_ key: String) -> Rx { regex[key]! }

    /// A pattern per label list, plain lists first, then the checked ones.
    private static func labels(_ rules: PrivateDataRules, template: String) -> [Label] {
        let plain = rules.fieldLabels.keys.sorted().map { ($0, false, rules.fieldLabels[$0]!) }
        let lists = plain + [("TAXID", true, rules.checkedLabels.taxID)]
        return lists.map { kind, checked, words in
            Label(kind: kind, checked: checked, rx: Rx(fill(template, alternation(words))))
        }
    }

    /// The template with its `<words...>` placeholder replaced by `words`.
    private static func fill(_ template: String, _ words: String) -> String {
        let ns = template as NSString
        guard let m = Rx("<words[^>]*>").search(ns) else { return template }
        return ns.substring(to: m.start) + words + ns.substring(from: m.end)
    }

    /// Label words longest first; spaces match runs of blanks, other characters are literal.
    private static func alternation(_ words: [String]) -> String {
        let ordered = words.sorted { a, b in
            let (la, lb) = (PyText.count(a), PyText.count(b))
            return la != lb ? la > lb : Array(a.unicodeScalars.map(\.value)).lexicographicallyPrecedes(b.unicodeScalars.map(\.value))
        }
        return ordered.map { word in
            PyText.split(word, " ").map { NSRegularExpression.escapedPattern(for: $0) }.joined(separator: "[ \\t]+")
        }.joined(separator: "|")
    }
}
