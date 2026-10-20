import AppKit
import Foundation

/// Sleep, screen lock and fast user switching: capture halts and resumes on its own (reader spec 1.5).
@MainActor final class SessionMonitor {
    /// Called whenever availability may have changed.
    var onChange: (() -> Void)?
    private var tokens: [(NotificationCenter, NSObjectProtocol)] = []
    private var asleep = false
    private var locked = false
    private var switchedOut = false

    /// True when the Mac is awake, unlocked and this user's session is on the console.
    var isAvailable: Bool { !asleep && !locked && !switchedOut && MacSession.isAvailable() }

    /// Starts listening.
    func start() {
        guard tokens.isEmpty else { return }
        let workspace = NSWorkspace.shared.notificationCenter
        observe(workspace, NSWorkspace.willSleepNotification) { $0.asleep = true }
        observe(workspace, NSWorkspace.didWakeNotification) { $0.asleep = false }
        observe(workspace, NSWorkspace.sessionDidResignActiveNotification) { $0.switchedOut = true }
        observe(workspace, NSWorkspace.sessionDidBecomeActiveNotification) { $0.switchedOut = false }
        let distributed = DistributedNotificationCenter.default()
        observe(distributed, Notification.Name("com.apple.screenIsLocked")) { $0.locked = true }
        observe(distributed, Notification.Name("com.apple.screenIsUnlocked")) { $0.locked = false }
    }

    /// Stops listening.
    func stop() {
        for (center, token) in tokens { center.removeObserver(token) }
        tokens = []
    }

    /// Adds one observer that updates a flag on the main thread.
    private func observe(_ center: NotificationCenter, _ name: Notification.Name,
                         _ update: @escaping @MainActor (SessionMonitor) -> Void) {
        let token = center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                update(self)
                self.onChange?()
            }
        }
        tokens.append((center, token))
    }
}
