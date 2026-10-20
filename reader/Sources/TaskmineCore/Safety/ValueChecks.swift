import Foundation

/// Check digits and classifications of private values (private_data_tables.json).
enum ValueChecks {
    static let rules = Rules.privateData
    static let otherTLDs: Set<String> = ["gov", "edu", "mil", "int", "org"]

    /// The ISO 7064 remainder of an IBAN of upper-case alphanumerics; a valid one gives 1.
    static func mod97(_ iban: String) -> Int {
        let scalars = Array(iban.unicodeScalars)
        guard scalars.count >= 4 else { return mod97Digits(scalars) }
        return mod97Digits(Array(scalars[4...]) + Array(scalars[..<4]))
    }

    /// The remainder of the base-36 digits written out in decimal.
    private static func mod97Digits(_ scalars: [Unicode.Scalar]) -> Int {
        var remainder = 0
        for c in scalars {
            let v = base36(c)
            remainder = (remainder * (v >= 10 ? 100 : 10) + v) % 97
        }
        return remainder
    }

    /// The base-36 value of one ASCII letter or digit.
    private static func base36(_ c: Unicode.Scalar) -> Int {
        switch c.value {
        case 48...57: return Int(c.value) - 48
        case 65...90: return Int(c.value) - 55
        case 97...122: return Int(c.value) - 87
        default: return 0
        }
    }

    /// True when the ASCII digits pass the Luhn check.
    static func luhn(_ digits: String) -> Bool {
        var total = 0
        for (index, c) in digits.unicodeScalars.reversed().enumerated() {
            let value = (Int(c.value) - 48) * (index % 2 == 1 ? 2 : 1)
            total += value > 9 ? value - 9 : value
        }
        return total % 10 == 0
    }

    /// The brand a card number's prefix and length name, or nil.
    static func cardBrand(_ digits: String) -> String? {
        let size = digits.unicodeScalars.count
        guard (13...19).contains(size) else { return nil }
        let two = Int(digits.prefix(2))!, three = Int(digits.prefix(3))!, four = Int(digits.prefix(4))!
        let rules: [(String, Bool)] = [
            ("VISA", digits.hasPrefix("4") && (size == 16 || size == 19)),
            ("MC", size == 16 && ((51...55).contains(two) || (2221...2720).contains(four))),
            ("AMEX", size == 15 && (two == 34 || two == 37)),
            ("DISCOVER", size >= 16 && (four == 6011 || two == 65 || (644...649).contains(three))),
            ("DINERS", size >= 14 && ((300...305).contains(three) || four == 3095 || [36, 38, 39].contains(two))),
            ("JCB", size >= 16 && (3528...3589).contains(four)),
            ("UNIONPAY", size >= 16 && two == 62),
        ]
        return rules.first { $0.1 }?.0
    }

    /// True when the sixteenth character is the check letter of the first fifteen.
    static func codiceFiscaleValid(_ value: String) -> Bool {
        let text = Array(value.uppercased().unicodeScalars)
        guard text.count == 16 else { return false }
        var total = 0
        for (index, c) in text[..<15].enumerated() {
            if index % 2 == 0 {
                guard let odd = oddValue(c) else { return false }
                total += odd
            } else {
                total += PyText.isASCIIDigit(c) ? Int(c.value) - 48 : Int(c.value) - 65
            }
        }
        return text[15].value == UInt32(65 + total % 26)
    }

    /// The odd-position value of a digit or letter, or nil for any other character.
    private static func oddValue(_ c: Unicode.Scalar) -> Int? {
        switch c.value {
        case 48...57: return rules.oddValues.digits[Int(c.value) - 48]
        case 65...90: return rules.oddValues.letters[Int(c.value) - 65]
        default: return nil
        }
    }

    /// The country of a calling code: three digits, then two, then one; else INT.
    static func callingCountry(_ digits: String) -> String {
        for n in [3, 2, 1] {
            if let country = rules.callingCodes[String(digits.prefix(n))] { return country }
        }
        return "INT"
    }

    /// webmail, other (public bodies and schools) or corp.
    static func emailType(_ address: String) -> String {
        let domain = PyText.rpartition(address, "@").2.lowercased()
        if rules.webmail.contains(domain) { return "webmail" }
        let labels = PyText.split(domain, ".")
        let last = labels[labels.count - 1]
        if otherTLDs.contains(last) { return "other" }
        if labels.count >= 3, ["gov", "edu", "ac"].contains(labels[labels.count - 2]), PyText.count(last) == 2 {
            return "other"
        }
        return "corp"
    }

    /// The phone number as digits with country code, and its country.
    static func phoneNormal(_ value: String) -> (String, String) {
        let text = PyText.strip(value)
        let digits = PyText.digits(text)
        let rx = PrivateDataPatterns.shared
        if PyText.startsWith(text, "+") { return (digits, callingCountry(digits)) }
        if PyText.startsWith(text, "00") && digits.count > 4 {
            let rest = String(digits.dropFirst(2))
            return (rest, callingCountry(rest))
        }
        let ns = digits as NSString
        if rx["italianMobileDigits"].match(ns) != nil || rx["italianLandlineDigits"].match(ns) != nil {
            return ("39" + digits, "IT")
        }
        return (digits, "INT")
    }
}
