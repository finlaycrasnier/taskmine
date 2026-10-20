// Lossless text tidy, normalisation version 2 (reader spec 6.1).
// Oracle: capture_normalization.json, 18 texts, 9 urls, 15 clock lines.
import Foundation
import Testing
import TaskmineCore

@Suite struct TidyTests {
    static let texts = Numbered.list("text", Fixtures.normalization.texts)
    static let urls = Numbered.list("url", Fixtures.normalization.urls)
    static let clockLines = Numbered.list("clock", Fixtures.normalization.clock_lines)

    @Test func counts() {
        #expect(Self.texts.count == 18)
        #expect(Self.urls.count == 9)
        #expect(Self.clockLines.count == 15)
    }

    @Test(arguments: texts)
    func tidy(_ c: Numbered<StringCase>) {
        let out = Tidy.text(c.value.input)
        let same = Array(out.utf8) == Array(c.value.expect.utf8)
        #expect(same, "\(c.testDescription): got \(out.debugDescription)")
    }

    @Test(arguments: texts)
    func tidyIsIdempotent(_ c: Numbered<StringCase>) {
        let out = Tidy.text(c.value.expect)
        let same = Array(out.utf8) == Array(c.value.expect.utf8)
        #expect(same, "\(c.testDescription): got \(out.debugDescription)")
    }

    @Test(arguments: urls)
    func url(_ c: Numbered<StringCase>) {
        let out = Tidy.url(c.value.input)
        let same = Array(out.utf8) == Array(c.value.expect.utf8)
        #expect(same, "\(c.testDescription): got \(out.debugDescription)")
    }

    @Test(arguments: clockLines)
    func clockLine(_ c: Numbered<BoolCase>) {
        #expect(Tidy.isClockLine(c.value.input) == c.value.expect, "\(c.testDescription): \(c.value.input)")
    }
}
