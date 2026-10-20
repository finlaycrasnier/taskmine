import AppKit
import Foundation

/// The frontmost app as the readers see it: process, bundle ID, name and kind.
struct AppInfo {
    let pid: pid_t
    let bundleID: String
    let name: String
    let bundlePath: String?
    let launch: Date?
    let kind: SurfaceKind

    /// The frontmost app, or nil.
    @MainActor static func frontmost() -> AppInfo? {
        NSWorkspace.shared.frontmostApplication.map(AppInfo.init)
    }

    /// The app of a running process.
    init(_ app: NSRunningApplication) {
        pid = app.processIdentifier
        bundleID = app.bundleIdentifier ?? ""
        name = app.localizedName ?? bundleID
        bundlePath = app.bundleURL?.path
        launch = app.launchDate
        kind = WebSurfaces.shared.kind(bundleID: bundleID, bundleURL: app.bundleURL)
    }
}
