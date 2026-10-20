// Sources/TaskmineCore/Rules and Tests/TaskmineCoreTests/Fixtures are copies of
// data/rules and data/fixtures. This suite fails when a copy drifts from data/.
import Foundation
import Testing

@Suite struct MirrorTests {
    /// reader/Tests/TaskmineCoreTests/MirrorTests.swift -> repository root.
    static let root = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
    static let reader = root.appendingPathComponent("reader")

    static func files(under dir: URL) -> [String] {
        let fm = FileManager.default
        guard let walker = fm.enumerator(at: dir, includingPropertiesForKeys: [.isRegularFileKey]) else {
            return []
        }
        var out: [String] = []
        for case let url as URL in walker where url.pathExtension == "json" {
            out.append(String(url.standardizedFileURL.path.dropFirst(dir.standardizedFileURL.path.count + 1)))
        }
        return out.sorted()
    }

    static func compare(copy: URL, original: URL) {
        let copied = files(under: copy)
        let originals = files(under: original)
        #expect(!originals.isEmpty, "no files under \(original.path)")
        #expect(copied == originals, "file lists differ: \(copy.path)")
        for name in originals {
            let a = try? Data(contentsOf: copy.appendingPathComponent(name))
            let b = try? Data(contentsOf: original.appendingPathComponent(name))
            #expect(a != nil && a == b, "\(name) differs from data/")
        }
    }

    @Test func rulesMirrorData() {
        Self.compare(
            copy: Self.reader.appendingPathComponent("Sources/TaskmineCore/Rules"),
            original: Self.root.appendingPathComponent("data/rules"))
    }

    @Test func fixturesMirrorData() {
        Self.compare(
            copy: Self.reader.appendingPathComponent("Tests/TaskmineCoreTests/Fixtures"),
            original: Self.root.appendingPathComponent("data/fixtures"))
    }

    @Test func bundledFixturesMatchSource() {
        Self.compare(
            copy: Fixtures.directory,
            original: Self.root.appendingPathComponent("data/fixtures"))
    }
}
