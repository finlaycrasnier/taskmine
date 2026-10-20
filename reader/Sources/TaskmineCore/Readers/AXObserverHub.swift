import ApplicationServices
import Foundation

/// Accessibility notifications from the frontmost app only (reader spec 2.1, 2.7).
@MainActor final class AXObserverHub {
    /// Notifications for snapshots and interactions.
    static let names = ["AXFocusedWindowChanged", "AXFocusedUIElementChanged", "AXValueChanged", "AXTitleChanged",
                        "AXSelectedTextChanged", "AXSelectedChildrenChanged"]

    /// Receives each notification name and its element.
    var onNotification: ((String, AXUIElement) -> Void)?
    private var observer: AXObserver?
    private(set) var pid: pid_t?

    /// Observes `pid`, detaching from any other process first.
    func attach(pid: pid_t, web: Bool) {
        if self.pid == pid, observer != nil { return }
        detach()
        var created: AXObserver?
        guard AXObserverCreate(pid, axObserverCallback, &created) == .success, let created else { return }
        let app = AX.application(pid)
        let refcon = Unmanaged.passUnretained(self).toOpaque()
        for name in Self.names + (web ? ["AXLoadComplete"] : []) {
            _ = AXObserverAddNotification(created, app, name as CFString, refcon)
        }
        CFRunLoopAddSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(created), .defaultMode)
        observer = created
        self.pid = pid
    }

    /// Stops observing.
    func detach() {
        if let observer {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(observer), .defaultMode)
        }
        observer = nil
        pid = nil
    }

    /// Delivers one notification.
    func deliver(_ name: String, _ element: AXUIElement) {
        onNotification?(name, element)
    }
}

/// The C callback: hands the notification to the hub on the main thread.
private func axObserverCallback(_ observer: AXObserver, _ element: AXUIElement, _ name: CFString,
                                _ refcon: UnsafeMutableRawPointer?) {
    guard let refcon else { return }
    let hub = Unmanaged<AXObserverHub>.fromOpaque(refcon).takeUnretainedValue()
    let text = name as String
    // The run loop source sits on the main run loop, so this runs on the main thread.
    nonisolated(unsafe) let target = element
    MainActor.assumeIsolated { hub.deliver(text, target) }
}
