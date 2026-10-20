// Secret rules and private-data coding (reader spec 5.2, 5.6; fixture semantics in section 10).
// Oracle: credential_cases.json, 495 cases and 57 private-data cases.
// Failures name the case id. Comparisons go through Bool values so no secret is printed.
import Foundation
import Testing
import TaskmineCore

private let redactedMarker = "[redacted]"

private func mode(_ kind: String) -> TextSafety.Mode {
    switch kind {
    case "title": return .title
    case "url": return .url
    default: return .text
    }
}

@Suite struct CredentialCasesTests {
    static let all = Fixtures.credential.cases
    static let textAndTitle = all.filter { $0.kind == "text" || $0.kind == "title" }
    static let urlCases = all.filter { $0.kind == "url" }
    static let redactCases = textAndTitle.filter { $0.expect == "redact" }
    static let secretCases = all.filter { $0.secret != nil }
    static let privateCases = Fixtures.credential.private_data.cases

    // MARK: counts

    @Test func sanitizerVersion() {
        #expect(TextSafety.sanitizerVersion == 2)
        #expect(Fixtures.credential.sanitizer_version == 2)
    }

    @Test func caseCounts() {
        let cases = Self.all
        #expect(cases.count == 495)
        #expect(cases.filter { $0.kind == "text" }.count == 288)
        #expect(cases.filter { $0.kind == "title" }.count == 198)
        #expect(cases.filter { $0.kind == "url" }.count == 9)
        #expect(cases.filter { $0.expect == "redact" }.count == 301)
        #expect(cases.filter { $0.expect == "exclude" }.count == 158)
        #expect(cases.filter { $0.expect == "keep" }.count == 27)
        #expect(cases.filter { $0.expect == "drop" }.count == 6)
        #expect(cases.filter { $0.expect == "url" }.count == 3)
        #expect(Set(cases.map(\.id)).count == 495)
        #expect(Fixtures.credential.values.count == 22)
    }

    @Test func privateDataCounts() {
        let cases = Self.privateCases
        #expect(cases.count == 57)
        #expect(cases.filter { $0.keyless == true }.count == 1)
        #expect(Set(cases.map(\.id)).count == 57)
        #expect(Fixtures.keyHex.count == 64)
    }

    // MARK: text and title cases

    @Test(arguments: textAndTitle)
    func textOrTitleCase(_ c: CredentialCase) {
        let source = Fixtures.build(c.parts)
        let result = TextSafety.evaluate(source, mode: mode(c.kind), limit: c.limit)

        if c.expect == "exclude" {
            #expect(result.excluded, "\(c.id): not excluded")
            #expect(result.text == nil, "\(c.id): excluded record kept text")
            if let reason = c.reason {
                let sameReason = result.reason == reason
                #expect(sameReason, "\(c.id): wrong reason \(result.reason ?? "nil"), expected \(reason)")
            }
            return
        }

        #expect(!result.excluded, "\(c.id): excluded")
        guard let text = result.text else {
            Issue.record("\(c.id): no text")
            return
        }

        switch c.expect {
        case "keep":
            let unchanged = text == source
            #expect(unchanged, "\(c.id): keep case changed")
            #expect(result.redactions == 0, "\(c.id): keep case has redactions")
        case "redact":
            #expect(result.redactions >= 1, "\(c.id): no redaction counted")
            let marked = text.contains(redactedMarker)
            #expect(marked, "\(c.id): no [redacted] marker")
        default:
            Issue.record("\(c.id): unknown expect \(c.expect) for a \(c.kind) case")
        }

        for kept in c.keep ?? [] {
            let survived = text.contains(kept)
            #expect(survived, "\(c.id): a listed substring did not survive")
        }

        if let name = c.secret {
            let secret = Fixtures.value(name)
            let leaked = text.contains(secret)
            #expect(!leaked, "\(c.id): secret value survived")
            if let n = c.absent_prefix {
                let prefixLeaked = text.contains(String(secret.prefix(n)))
                #expect(!prefixLeaked, "\(c.id): secret prefix survived")
            }
        }

        if let limit = c.limit {
            #expect(text.scalarCount <= limit, "\(c.id): longer than the limit")
        }

        // Idempotence: the output holds no credential and a second pass changes nothing.
        let detected = TextSafety.containsCredential(text)
        #expect(!detected, "\(c.id): output still trips containsCredential")
        let again = TextSafety.evaluate(text, mode: mode(c.kind), limit: c.limit)
        #expect(!again.excluded, "\(c.id): second pass excluded the output")
        let stable = again.text == text
        #expect(stable, "\(c.id): second pass changed the output")
    }

    /// containsCredential runs the same rules (spec 5.2), so every input that is redacted
    /// must also be detected.
    @Test(arguments: redactCases)
    func redactInputIsDetected(_ c: CredentialCase) {
        let detected = TextSafety.containsCredential(Fixtures.build(c.parts))
        #expect(detected, "\(c.id): input not detected")
    }

    @Test(arguments: secretCases)
    func secretNeverInOutput(_ c: CredentialCase) {
        let source = Fixtures.build(c.parts)
        let out = c.kind == "url"
            ? TextSafety.sanitizeURL(source)
            : TextSafety.evaluate(source, mode: mode(c.kind), limit: c.limit).text
        if let out, let name = c.secret {
            let leaked = out.contains(Fixtures.value(name))
            #expect(!leaked, "\(c.id): secret value survived")
        }
    }

    // MARK: url cases

    @Test(arguments: urlCases)
    func urlCase(_ c: CredentialCase) {
        #expect(c.expect == "url" || c.expect == "drop", "\(c.id): unknown expect")
        let source = Fixtures.build(c.parts)
        let expected: String? = c.expect == "url" ? c.url : nil

        let sanitized = TextSafety.sanitizeURL(source)
        let same = sanitized == expected
        #expect(same, "\(c.id): sanitizeURL gave \(sanitized == nil ? "nil" : "a different URL")")

        let result = TextSafety.evaluate(source, mode: .url)
        let sameText = result.text == expected
        #expect(sameText, "\(c.id): url mode text differs from sanitizeURL")
        #expect(result.excluded == (expected == nil), "\(c.id): url mode excluded flag")

        if let expected {
            let stable = TextSafety.sanitizeURL(expected) == expected
            #expect(stable, "\(c.id): sanitizeURL is not idempotent")
        }
    }

    // MARK: private data

    @Test(arguments: privateCases)
    func privateDataCase(_ c: PrivateDataCase) {
        let coder = PrivateDataCoder(keyHex: c.keyless == true ? nil : Fixtures.keyHex)
        let (text, counts) = coder.code(c.parts.joined())
        #expect(text == c.coded, "\(c.id)")
        #expect(counts == c.counts, "\(c.id)")
    }

    @Test(arguments: privateCases)
    func codingIsIdempotent(_ c: PrivateDataCase) {
        let coder = PrivateDataCoder(keyHex: c.keyless == true ? nil : Fixtures.keyHex)
        #expect(coder.code(c.coded).text == c.coded, "\(c.id)")
    }

    @Test func keylessHashesNothing() {
        let (text, counts) = PrivateDataCoder(keyHex: nil).code("Contact mario.rossi@acmecorp.it")
        #expect(text == "Contact [EMAIL·corp·????]")
        #expect(counts == ["EMAIL": 1])
    }

    @Test func invalidKeyActsAsKeyless() {
        let (text, _) = PrivateDataCoder(keyHex: "not-hex").code("Contact mario.rossi@acmecorp.it")
        #expect(text == "Contact [EMAIL·corp·????]")
    }

    /// A form field label goes in front for labelled detection and is removed again
    /// (spec 2.7 final value rule, 5.6). Expected codes come from the labelled fixture
    /// cases with the same label and value.
    @Test(arguments: [
        ("Salary", "54,000 EUR per year", "[SALARY·331e]", ["SALARY": 1]),
        ("IBAN", "12345", "[IBAN·??·b6ab]", ["IBAN": 1]),
        ("Email", "not an address", "[EMAIL·other·f247]", ["EMAIL": 1]),
        ("Partita IVA", "01234567897", "[TAXID·IT·1514]", ["TAXID": 1]),
        ("Partita IVA", "12345", "12345", [:]),
        ("VAT", "22%", "22%", [:]),
    ] as [(String, String, String, [String: Int])])
    func fieldLabel(_ field: String, _ value: String, _ coded: String, _ counts: [String: Int]) {
        let (text, got) = PrivateDataCoder(keyHex: Fixtures.keyHex).code(value, field: field)
        #expect(text == coded)
        #expect(got == counts)
    }

    @Test func noFieldIsPlainCoding() {
        let coder = PrivateDataCoder(keyHex: Fixtures.keyHex)
        let source = "Pay to IT60 X054 2811 1010 0000 0123 456 please"
        let plain = coder.code(source)
        let withNil = coder.code(source, field: nil)
        #expect(plain.text == withNil.text)
        #expect(plain.counts == withNil.counts)
    }
}
