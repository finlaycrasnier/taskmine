import AppKit
import ApplicationServices
import Foundation

/// Clicks, focus changes, scrolls, Command shortcuts and edit bursts, without an event tap (reader spec 1.3, 2.7).
@MainActor final class InteractionTracker {
    /// Receives each interaction.
    var emit: ((InteractionRecord) -> Void)?
    private let gate: SurfaceGate
    private let burst = EditBurst()
    private var bursts: [String: BurstContext] = [:]
    private var monitors: [Any] = []
    private var lastScroll: Date?
    /// The last surface that passed, reused for 2 s so each click does not re-inspect the window.
    private var recent: (key: String, surface: Surface, at: Date)?
    private(set) var running = false

    /// A tracker that checks every surface with `gate`.
    init(gate: SurfaceGate) { self.gate = gate }

    /// Installs the global monitors and records `window_focus` for the frontmost app.
    func start() {
        guard !running else { return }
        running = true
        let mask: NSEvent.EventTypeMask = [.leftMouseDown, .rightMouseDown, .scrollWheel, .keyDown]
        if let monitor = NSEvent.addGlobalMonitorForEvents(matching: mask, handler: { [weak self] event in
            let type = event.type
            let command = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            let key = type == .keyDown && command.contains(.command) ? event.characters(byApplyingModifiers: []) : nil
            MainActor.assumeIsolated { self?.handle(type, command, key) }
        }) {
            monitors.append(monitor)
        }
        record("window_focus", trigger: "tracker_started")
    }

    /// Ends open bursts and removes the monitors.
    func stop() {
        guard running else { return }
        finish(burst.interrupt())
        monitors.forEach(NSEvent.removeMonitor)
        monitors = []
        recent = nil
        running = false
    }

    /// The frontmost app changed.
    func activated() { record("window_focus", trigger: "application_activated") }

    /// One Accessibility notification from the frontmost app.
    func notification(_ name: String, element: AXUIElement) {
        switch name {
        case "AXFocusedWindowChanged", "AXFocusedUIElementChanged":
            record("focus", trigger: "accessibility_notification", element: element)
        case "AXValueChanged": edit(element)
        default: break
        }
    }

    /// Writes burst revisions and ends that fell due.
    func tick() { finish(burst.tick()) }

    /// One global input event; a key without Command is never read.
    private func handle(_ type: NSEvent.EventType, _ flags: NSEvent.ModifierFlags, _ key: String?) {
        switch type {
        case .leftMouseDown, .rightMouseDown: click()
        case .scrollWheel:
            let now = Date()
            if let lastScroll, now.timeIntervalSince(lastScroll) < 2 { return }
            lastScroll = now
            record("scroll", trigger: "global_event")
        case .keyDown:
            guard flags.contains(.command), !MacSession.secureInputOn(), let name = Self.shortcut(flags, key) else { return }
            record("shortcut", trigger: "global_event", shortcut: name)
        default: break
        }
    }

    /// A click: the element under the pointer, only when the frontmost process owns it.
    private func click() {
        guard let app = AppInfo.frontmost(), let screen = NSScreen.screens.first else { return }
        let point = NSEvent.mouseLocation
        var hit: AXUIElement?
        let system = AXUIElementCreateSystemWide()
        guard AXUIElementCopyElementAtPosition(system, Float(point.x), Float(screen.frame.maxY - point.y), &hit)
                == .success, let element = hit, AX.pid(element) == app.pid else { return }
        record("click", trigger: "global_event", element: element, skipSecure: true)
    }

    /// `cmd[+ctrl][+opt][+shift]+key` for an allowed key, else nil.
    static func shortcut(_ flags: NSEvent.ModifierFlags, _ key: String?) -> String? {
        guard let key = key?.lowercased(), key.unicodeScalars.count == 1,
              WebSurfaces.shared.shortcutKeys.contains(key) else { return nil }
        var parts = ["cmd"]
        if flags.contains(.control) { parts.append("ctrl") }
        if flags.contains(.option) { parts.append("opt") }
        if flags.contains(.shift) { parts.append("shift") }
        return (parts + [key]).joined(separator: "+")
    }

    /// Records one non-edit interaction after ending open bursts.
    private func record(_ type: String, trigger: String, element: AXUIElement? = nil, shortcut: String? = nil,
                        skipSecure: Bool = false) {
        guard running, let app = AppInfo.frontmost() else { return }
        finish(burst.interrupt())
        guard let (surface, crumb) = checked(app, element), !(skipSecure && crumb.secure) else { return }
        var metadata = surface.metadata.merging(crumb.metadata) { a, _ in a }
        metadata["capture_method"] = "native_interaction"
        metadata["trigger"] = trigger
        metadata["interaction_type"] = type
        metadata["shortcut"] = shortcut
        emit?(InteractionRecord(reading: Self.reading(surface.title, metadata), burst: nil, at: Date()))
    }

    /// One value change: the first in a window opens a burst and writes an `edit` at once.
    private func edit(_ element: AXUIElement) {
        guard running, !MacSession.secureInputOn(), let app = AppInfo.frontmost(), AX.pid(element) == app.pid,
              let window = AX.element(element, "AXWindow"),
              let focused = AX.element(AX.application(app.pid), "AXFocusedUIElement"), CFEqual(focused, element)
        else { return }
        let key = "\(app.pid)|\(CFHash(window))"
        let now = Date()
        if bursts[key] == nil {
            guard let (surface, crumb) = checked(app, element), !crumb.secure else { return }
            var metadata = surface.metadata.merging(crumb.metadata) { a, _ in a }
            metadata["capture_method"] = "native_interaction"
            metadata["trigger"] = "accessibility_notification"
            metadata["interaction_type"] = "edit"
            bursts[key] = BurstContext(element: element, app: app, policyVersion: surface.policyVersion,
                                       domain: surface.domain, base: Self.reading(surface.title, metadata))
        }
        if let event = burst.edit(window: key, at: now), let context = bursts[key] {
            emit?(InteractionRecord(reading: context.base, burst: event, at: now))
        }
    }

    /// Emits burst reports; an end reads the final value once.
    private func finish(_ events: [BurstEvent]) {
        for event in events {
            guard let context = bursts[event.window] else { continue }
            var reading = context.base
            if case .ended = event.kind {
                bursts[event.window] = nil
                let (text, omitted) = FinalValueCheck(gate: gate).value(for: context)
                reading.text = text ?? ""
                reading.metadata["value_omitted"] = omitted
            }
            emit?(InteractionRecord(reading: reading, burst: event, at: Date()))
        }
    }

    /// The surface and breadcrumb for an interaction, or nil when steps 1 to 8 refuse it.
    private func checked(_ app: AppInfo, _ element: AXUIElement?) -> (Surface, Breadcrumb)? {
        guard let surface = surface(app) else { return nil }
        let target = element ?? AX.element(AX.application(app.pid), "AXFocusedUIElement")
        let crumb = target.map { Breadcrumb.read($0, appName: app.name) } ?? Breadcrumb()
        return (surface, crumb)
    }

    /// The checked surface of the focused window; a pass is reused for 2 s while window and title stay.
    private func surface(_ app: AppInfo) -> Surface? {
        let appElement = AX.application(app.pid)
        guard let window = AX.element(appElement, "AXFocusedWindow") else { return nil }
        let key = "\(app.pid)|\(CFHash(window))|\(EventContent.sha256(AX.string(window, "AXTitle") ?? ""))"
        let focused = AX.element(appElement, "AXFocusedUIElement")
        let secureFocus = focused.map { AX.isSecure(AX.string($0, "AXRole"), AX.string($0, "AXSubrole")) } ?? false
        if let recent, recent.key == key, Date().timeIntervalSince(recent.at) < 2, !secureFocus,
           gate.appCheck(app, provenance: "native_interaction") == nil, gate.policy?.version == recent.surface.policyVersion {
            return recent.surface
        }
        recent = nil
        guard case .pass(let surface) = gate.check(app, provenance: "native_interaction") else { return nil }
        if app.kind == .electron,
           BrowserScan.run(surface.window).webAreas.contains(where: { gate.electronAreaCheck($0) != nil }) { return nil }
        recent = (key, surface, Date())
        return surface
    }

    /// An interaction reading.
    private static func reading(_ title: String, _ metadata: [String: String]) -> Reading {
        Reading(title: title, text: "", metadata: metadata, provenance: "native_interaction", kind: "interaction")
    }
}
