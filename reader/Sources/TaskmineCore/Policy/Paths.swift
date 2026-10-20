import CryptoKit
import Foundation

/// The files under TASKMINE_HOME and the core origin (docs/ARCHITECTURE.md "Files").
public struct Paths: Sendable {
    /// The private home folder, mode 0700.
    public let home: URL
    /// The loopback port of the core.
    public let port: Int

    /// Paths under `home` for a core on `port`.
    public init(home: URL, port: Int) {
        self.home = home
        self.port = port
    }

    /// Home from TASKMINE_HOME (default ~/.taskmine) and port from TASKMINE_PORT (default 8797).
    public static func fromEnvironment(_ env: [String: String] = ProcessInfo.processInfo.environment) -> Paths {
        let raw = env["TASKMINE_HOME"].flatMap { $0.isEmpty ? nil : $0 } ?? "~/.taskmine"
        let expanded = (raw as NSString).expandingTildeInPath
        let home = URL(fileURLWithPath: expanded, isDirectory: true).standardizedFileURL
        let port = env["TASKMINE_PORT"].flatMap { Int($0.trimmingCharacters(in: .whitespaces)) }
            .flatMap { (1...65_535).contains($0) ? $0 : nil } ?? 8_797
        return Paths(home: home, port: port)
    }

    /// The upload queue.
    public var readerDB: URL { home.appendingPathComponent("reader.db") }
    /// The key behind personal-data codes, made by the app.
    public var codingKey: URL { home.appendingPathComponent("coding.key") }
    /// The upload token, made by the core.
    public var token: URL { home.appendingPathComponent("token") }
    /// The status file for `taskmine doctor`: counts and fixed codes only.
    public var status: URL { home.appendingPathComponent("status.json") }
    /// The journal of web-tree attributes this app turned on (reader spec 3.2).
    public var wakeJournal: URL { home.appendingPathComponent("web-wake.json") }
    /// The core origin, loopback only.
    public var coreOrigin: URL { URL(string: "http://127.0.0.1:\(port)")! }

    /// Creates the home folder if needed and sets it to 0700.
    public func prepareHome() throws {
        let fm = FileManager.default
        try fm.createDirectory(at: home, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: home.path)
    }

    /// The coding key as 64 hex digits; the first run creates it from 32 random bytes, mode 0600.
    public func codingKeyHex() throws -> String {
        if FileManager.default.fileExists(atPath: codingKey.path) {
            return (try String(contentsOf: codingKey, encoding: .utf8)).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        let hex = SymmetricKey(size: .bits256).withUnsafeBytes { $0.map { String(format: "%02x", $0) }.joined() }
        try Self.createPrivate(codingKey, Data((hex + "\n").utf8))
        return hex
    }

    /// The upload token, or nil when it is missing, not a regular file, too large or malformed.
    public func readToken() -> String? {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: token.path),
              attrs[.type] as? FileAttributeType == .typeRegular,
              let size = attrs[.size] as? Int, size <= 16_384,
              let text = try? String(contentsOf: token, encoding: .utf8) else { return nil }
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, !value.unicodeScalars.contains(where: { $0.properties.isWhitespace }) else { return nil }
        return value
    }

    /// Writes `data` to a new file with mode 0600; fails if the file exists.
    static func createPrivate(_ url: URL, _ data: Data) throws {
        let fd = open(url.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
        guard fd >= 0 else { throw CocoaError(.fileWriteNoPermission) }
        defer { close(fd) }
        let written = data.withUnsafeBytes { write(fd, $0.baseAddress, $0.count) }
        guard written == data.count else { throw CocoaError(.fileWriteUnknown) }
    }

    /// Replaces `url` atomically with `data`, mode 0600.
    public static func writePrivateAtomically(_ url: URL, _ data: Data) throws {
        let temp = url.deletingLastPathComponent()
            .appendingPathComponent(".\(url.lastPathComponent).\(UUID().uuidString.lowercased()).tmp")
        try createPrivate(temp, data)
        guard rename(temp.path, url.path) == 0 else {
            try? FileManager.default.removeItem(at: temp)
            throw CocoaError(.fileWriteUnknown)
        }
    }
}
