import ApplicationServices
import CryptoKit
import Foundation

/// Window references and document IDs that hold no title or path (reader spec 2.9).
@MainActor final class WindowIdentity {
    /// One remembered window.
    private struct Entry {
        let pid: pid_t
        let launch: Date?
        let element: AXUIElement
        let ref: String
    }

    private var entries: [Entry] = []
    private let salt = SymmetricKey(size: .bits256).withUnsafeBytes { $0.map { String(format: "%02x", $0) }.joined() }

    /// The `window:<uuid>` of a window, made on first sight; at most 128 are kept.
    func ref(pid: pid_t, launch: Date?, window: AXUIElement) -> String {
        if let hit = entries.first(where: { $0.pid == pid && $0.launch == launch && CFEqual($0.element, window) }) {
            return hit.ref
        }
        let ref = "window:" + UUID().uuidString.lowercased()
        entries.append(Entry(pid: pid, launch: launch, element: window, ref: ref))
        if entries.count > 128 { entries.removeFirst(entries.count - 128) }
        return ref
    }

    /// The window element behind a reference, or nil when it was dropped.
    func element(for ref: String) -> AXUIElement? {
        entries.first { $0.ref == ref }?.element
    }

    /// `document:<sha256>` for a regular local file, salted per run; nil for anything else.
    func documentID(_ url: URL) -> String? {
        let host = url.host ?? ""
        guard url.isFileURL, url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              host.isEmpty || host == "localhost" else { return nil }
        let standard = url.standardizedFileURL
        guard Self.isSafeFile(standard),
              let attrs = try? FileManager.default.attributesOfItem(atPath: standard.path),
              attrs[.type] as? FileAttributeType == .typeRegular,
              !standard.path.hasSuffix("/workbench.html") else { return nil }
        let digest = SHA256.hash(data: Data("\(salt)|\(standard.absoluteString)".utf8))
        return "document:" + digest.map { String(format: "%02x", $0) }.joined()
    }

    /// True when a local file address names no secret file and survives the URL rules.
    static func isSafeFile(_ url: URL) -> Bool {
        TextSafety.sanitizeURL(url.absoluteString) != nil && TextSafety.exclusion(url.path) == nil
    }
}
