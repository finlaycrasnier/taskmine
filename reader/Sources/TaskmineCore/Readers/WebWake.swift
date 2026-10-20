import AppKit
import ApplicationServices
import Foundation

/// Turns on the web accessibility tree of Chromium, Gecko and Electron apps and undoes it (reader spec 3.2).
@MainActor final class WebWake {
    /// One attribute this app turned on.
    private struct Entry: Codable, Equatable {
        let pid: Int32
        let attribute: String
        let bundleID: String
    }

    private let journal: URL
    private var tried: Set<Int32> = []
    private var entries: [Entry] = []

    /// A waker that journals to `journal`.
    init(journal: URL) { self.journal = journal }

    /// Wakes the web tree once per process; true when it changed something now.
    func wake(pid: pid_t, kind: SurfaceKind, bundleID: String) -> Bool {
        guard kind.isWeb, !tried.contains(pid) else { return false }
        tried.insert(pid)
        let attribute = kind == .gecko ? "AXEnhancedUserInterface" : "AXManualAccessibility"
        let app = AX.application(pid)
        if AX.bool(app, attribute) == true { return false }
        guard AX.set(app, attribute, true) else { return false }
        entries.append(Entry(pid: pid, attribute: attribute, bundleID: bundleID))
        save()
        return true
    }

    /// Turns off what this app turned on, for processes still running.
    func restoreAll() {
        for entry in entries where Self.running(entry) { AX.set(AX.application(entry.pid), entry.attribute, false) }
        entries = []
        tried = []
        save()
    }

    /// Undoes what a crashed run left on, skipping process IDs now owned by another app.
    func recoverFromJournal() {
        guard let data = try? Data(contentsOf: journal),
              let old = try? JSONDecoder().decode([Entry].self, from: data) else { return }
        entries = old
        restoreAll()
    }

    /// True when the process still runs the same app.
    private static func running(_ entry: Entry) -> Bool {
        NSRunningApplication(processIdentifier: entry.pid)?.bundleIdentifier?.lowercased() == entry.bundleID.lowercased()
    }

    /// Writes the journal, mode 0600.
    private func save() {
        guard let data = try? JSONEncoder().encode(entries) else { return }
        try? Paths.writePrivateAtomically(journal, data)
    }
}
