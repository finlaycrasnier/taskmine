import Foundation

/// "Open at login" as a per-user LaunchAgent; off until the user turns it on.
enum LoginItem {
    private static let label = "app.taskmine.reader"

    /// The LaunchAgent file.
    private static var plist: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(label).plist")
    }

    /// True when the LaunchAgent exists.
    static var isEnabled: Bool { FileManager.default.fileExists(atPath: plist.path) }

    /// Writes or removes the LaunchAgent for this executable.
    static func set(_ enabled: Bool) throws {
        guard enabled else {
            if isEnabled { try FileManager.default.removeItem(at: plist) }
            return
        }
        let executable = Bundle.main.executableURL?.path ?? CommandLine.arguments[0]
        var environment: [String: String] = [:]
        for key in ["TASKMINE_HOME", "TASKMINE_PORT"] {
            if let value = ProcessInfo.processInfo.environment[key] { environment[key] = value }
        }
        let body: [String: Any] = ["Label": label, "ProgramArguments": [executable], "RunAtLoad": true,
                                   "ProcessType": "Interactive", "EnvironmentVariables": environment]
        let data = try PropertyListSerialization.data(fromPropertyList: body, format: .xml, options: 0)
        try FileManager.default.createDirectory(at: plist.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: plist, options: .atomic)
    }
}
