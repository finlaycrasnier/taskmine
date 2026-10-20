// Byte-identical sanitizer output shared with the Python core (reader spec 10).
// Oracle: sanitizer_golden.json, 494 cases, 164 expect null.
import Foundation
import Testing
import TaskmineCore

@Suite struct GoldenTests {
    static let cases = Fixtures.golden.cases

    @Test func counts() {
        #expect(Self.cases.count == 494)
        #expect(Self.cases.filter { $0.kind == "text" }.count == 287)
        #expect(Self.cases.filter { $0.kind == "title" }.count == 198)
        #expect(Self.cases.filter { $0.kind == "url" }.count == 9)
        #expect(Self.cases.filter { $0.output == nil }.count == 164)
        #expect(Set(Self.cases.map(\.id)).count == 494)
        #expect(Fixtures.golden.sanitizer_version == 2)
        #expect(Fixtures.golden.min_supported_version == 2)
        #expect(TextSafety.sanitizerVersion == Fixtures.golden.sanitizer_version)
    }

    @Test(arguments: cases)
    func golden(_ c: GoldenCase) {
        let source = c.input_parts.joined()
        let result: TextSafety.Result
        switch c.kind {
        case "url":
            let sanitized = TextSafety.sanitizeURL(source)
            let same = sanitized == c.output
            #expect(same, "\(c.id): sanitizeURL differs")
            result = TextSafety.evaluate(source, mode: .url)
        case "title":
            result = TextSafety.evaluate(source, mode: .title)
        default:
            result = TextSafety.evaluate(source, mode: .text)
        }
        // Byte equality: compare the UTF-8 bytes, not Swift's canonical String equality.
        let same = result.text.map { Array($0.utf8) } == c.output.map { Array($0.utf8) }
        #expect(same, "\(c.id): output differs")
        #expect(result.excluded == (c.output == nil), "\(c.id): excluded flag")
    }
}
