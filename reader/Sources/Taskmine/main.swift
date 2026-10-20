import AppKit

// The menu-bar app: an accessory app with one status item; every launch starts paused.
// Files the app and SQLite create (queue, WAL, status, key) are private to the user.
umask(0o077)

MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)
    withExtendedLifetime(delegate) { app.run() }
}
