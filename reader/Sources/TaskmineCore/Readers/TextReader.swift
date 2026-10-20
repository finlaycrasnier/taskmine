import AppKit
import ApplicationServices
import Foundation

/// The Accessibility reader: window text in reading order, and interactions through its tracker (spec 2, 3).
@MainActor public final class TextReader: @MainActor Reader {
    public let provenance = "native_accessibility"
    /// The last snapshot's outcome.
    public private(set) var lastOutcome: SnapshotOutcome = .skipped(nil)
    /// Called when a debounced snapshot falls due, with its trigger.
    var onSnapshotDue: ((String) -> Void)?
    let gate: SurfaceGate
    let tracker: InteractionTracker
    private let hub = AXObserverHub()
    private var scheduler = CaptureScheduler()
    private var timer: Timer?
    private var activation: NSObjectProtocol?
    private var candidates: [String: OCRCandidate] = [:]
    private var lastProbe: String?
    private static let localFile = Rx("file://[^\\s<>\"']+")

    /// A reader that checks surfaces with `gate`.
    init(gate: SurfaceGate) {
        self.gate = gate
        tracker = InteractionTracker(gate: gate)
        hub.onNotification = { [weak self] name, element in self?.notified(name, element) }
    }

    /// Observes the frontmost app; throws when Accessibility is not granted.
    public func start() throws {
        guard MacSession.accessibilityGranted else { throw ReaderError.accessibilityUnavailable }
        if activation == nil {
            activation = NSWorkspace.shared.notificationCenter.addObserver(
                forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.activated() }
            }
        }
        attachFrontmost()
        syncInteractions()
    }

    /// Stops observers, the tracker and the timer, and undoes web-tree wakes.
    public func stop() {
        if let activation { NSWorkspace.shared.notificationCenter.removeObserver(activation) }
        activation = nil
        hub.detach()
        tracker.stop()
        timer?.invalidate()
        timer = nil
        candidates = [:]
        scheduler.reset()
        gate.wake.restoreAll()
    }

    /// Starts or stops the interaction tracker as the policy allows `native_interaction`.
    func syncInteractions() {
        let allowed = activation != nil && gate.policy?.allowedProvenance.contains("native_interaction") == true
        if allowed { tracker.start() } else { tracker.stop() }
    }

    /// The focused window of the frontmost app, or nil with `lastOutcome` set.
    public func frontmostWindow() -> WindowRef? {
        guard let app = AppInfo.frontmost() else { lastOutcome = .skipped(nil); return nil }
        if let stop = gate.appCheck(app, provenance: provenance) { lastOutcome = Self.outcome(stop); return nil }
        guard let window = AX.element(AX.application(app.pid), "AXFocusedWindow") else {
            lastOutcome = .skipped(.secureOrUninspectableSurface)
            return nil
        }
        return WindowRef(pid: app.pid, ref: gate.identity.ref(pid: app.pid, launch: app.launch, window: window))
    }

    /// One snapshot of the focused window, or nil with `lastOutcome` saying why.
    public func snapshot(of window: WindowRef) -> Reading? {
        candidates[window.ref] = nil
        guard let app = AppInfo.frontmost(), app.pid == window.pid else { lastOutcome = .skipped(nil); return nil }
        let result = gate.check(app, provenance: provenance)
        rescheduleAfterWake()
        guard case .pass(let surface) = result else { lastOutcome = Self.outcome(result); return nil }
        guard surface.windowRef == window.ref else { lastOutcome = .skipped(nil); return nil }
        let reading = read(surface)
        scheduler.walked(at: Date())
        lastProbe = probe()
        return reading
    }

    /// Walks the surface, re-checks it and builds the reading.
    private func read(_ surface: Surface) -> Reading? {
        let web = surface.app.kind.isWeb
        let electronCheck: ((AXUIElement) -> String?)? = surface.app.kind == .electron
            ? { [gate] in gate.electronAreaCheck($0) } : nil
        let limits: WalkLimits = web ? .web : .native
        let walk = TextWalk(limits: limits, webAreaCheck: electronCheck).run(surface.walkRoot)
        if let blocked = walk.blocked {
            lastOutcome = .withheld(blocked == "secure_field" ? "secure_surface" : blocked)
            return nil
        }
        if let changed = changed(surface) { lastOutcome = .withheld(changed); return nil }
        let joined = Self.replaceLocalFiles(walk.lines.joined(separator: "\n"))
        guard let text = TextSafety.evaluate(joined, mode: .text, limit: limits.storedLimit).text else {
            lastOutcome = .withheld("credential_surface")
            return nil
        }
        if text.isEmpty && (!walk.gaps.isEmpty || surface.title.isEmpty) {
            lastOutcome = .skipped(.noVisibleContext)
            return nil
        }
        if let reason = ocrReason(surface, walk) {
            candidates[surface.windowRef] = OCRCandidate(surface: surface, reason: reason,
                                                         textHash: EventContent.sha256(text), at: Date())
        }
        var metadata = surface.metadata
        metadata["capture_method"] = provenance
        lastOutcome = .captured
        return Reading(title: surface.title, text: text, metadata: metadata, provenance: provenance, kind: "screen_text")
    }

    /// The held OCR candidate for a window.
    func ocrCandidate(for window: WindowRef) -> OCRCandidate? { candidates[window.ref] }

    /// The withheld category when the window changed during the walk (spec 2.3 step 13), else nil.
    func changed(_ s: Surface) -> String? {
        guard let window = AX.element(AX.application(s.app.pid), "AXFocusedWindow"), CFEqual(window, s.window) else {
            return "secure_surface"
        }
        let title = AX.string(window, "AXTitle") ?? ""
        if title != s.title { return TextSafety.evaluate(title, mode: .title).excluded ? "credential_surface" : "secure_surface" }
        if s.app.kind.isWeb {
            let scan = BrowserScan.run(window)
            if scan.failed { return "secure_surface" }
            if s.app.kind.isBrowser, scan.page()?.url != s.url { return "secure_surface" }
            return nil
        }
        if (AX.url(window, "AXDocument") ?? AX.url(window, "AXURL")) != s.url { return "credential_surface" }
        return gate.inspector(for: s.app).inspect(s.walkRoot) == nil ? nil : "secure_surface"
    }

    /// The re-checks before and after pixels (spec 7.5); true when nothing changed.
    func stillValid(_ s: Surface) -> Bool {
        guard MacSession.isAvailable(), !MacSession.secureInputOn(),
              NSWorkspace.shared.frontmostApplication?.processIdentifier == s.app.pid,
              gate.policy?.version == s.policyVersion, gate.policy?.state == .active else { return false }
        if let focused = AX.element(AX.application(s.app.pid), "AXFocusedUIElement"),
           AX.isSecure(AX.string(focused, "AXRole"), AX.string(focused, "AXSubrole")) { return false }
        if let host = s.domain, host != "local-file", gate.policy?.excludes(host: host) != false { return false }
        return changed(s) == nil
    }

    /// The backstop poll rule (spec 2.2): true when the window should be walked again.
    func pollShouldWalk(at now: Date = Date()) -> Bool {
        let current = probe()
        let changed = current != lastProbe
        lastProbe = current
        return scheduler.pollShouldWalk(probeChanged: changed, idle: MacSession.idleSeconds() >= 120, at: now)
    }

    /// `pid|CFHash(window)|focusedRole|characters|sha256(title)` of the frontmost window.
    private func probe() -> String? {
        guard let app = AppInfo.frontmost() else { return nil }
        let element = AX.application(app.pid)
        guard let window = AX.element(element, "AXFocusedWindow") else { return "\(app.pid)|none" }
        let focused = AX.element(element, "AXFocusedUIElement")
        let role = focused.flatMap { AX.string($0, "AXRole") } ?? ""
        let count = focused.flatMap { AX.int($0, "AXNumberOfCharacters") } ?? -1
        return "\(app.pid)|\(CFHash(window))|\(role)|\(count)|\(EventContent.sha256(AX.string(window, "AXTitle") ?? ""))"
    }

    /// Content OCR reason (spec 7.2), or nil.
    private func ocrReason(_ s: Surface, _ walk: WalkResult) -> String? {
        if s.app.kind == .electron || (s.app.kind.isWeb && !walk.isComplete) { return nil }
        if let url = s.url, WebSurfaces.shared.isCanvasDocument(url) { return "canvas_document" }
        guard walk.isComplete, walk.contentScalars < 160,
              walk.furnitureScalars > max(walk.contentScalars, 40) else { return nil }
        return "interface_text"
    }

    /// One notification: routed to the tracker and the debounce.
    private func notified(_ name: String, _ element: AXUIElement) {
        if tracker.running { tracker.notification(name, element: element) }
        switch name {
        case "AXValueChanged", "AXSelectedTextChanged": scheduler.note(.typing, at: Date())
        case "AXSelectedChildrenChanged": return
        default: scheduler.note(.structural, at: Date())
        }
        arm()
    }

    /// A new frontmost app: observe it, reset intervals, record window focus.
    private func activated() {
        attachFrontmost()
        scheduler.reset()
        scheduler.note(.structural, at: Date())
        arm()
        if tracker.running { tracker.activated() }
    }

    /// Attaches the observers to the frontmost app.
    private func attachFrontmost() {
        guard let app = AppInfo.frontmost() else { return hub.detach() }
        hub.attach(pid: app.pid, web: app.kind.isWeb)
    }

    /// After a web-tree wake that changed something, snapshot again 1 s later.
    private func rescheduleAfterWake() {
        guard gate.wokeAt != nil else { return }
        gate.wokeAt = nil
        scheduler.schedule(after: 1.0, at: Date())
        arm()
    }

    /// Arms the one-shot timer for the due time.
    private func arm() {
        timer?.invalidate()
        guard let due = scheduler.dueAt, activation != nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: max(0.05, due.timeIntervalSinceNow), repeats: false) { [weak self] _ in
            MainActor.assumeIsolated { self?.fire() }
        }
    }

    /// The timer fired: snapshot unless the rate floor moved it.
    private func fire() {
        timer = nil
        guard scheduler.isDue(at: Date()) else { return arm() }
        guard scheduler.admit(at: Date()) else { return arm() }
        onSnapshotDue?("accessibility_notification")
    }

    /// Marks the poll's own snapshot as an attempt for the rate floor; false inside the 0.5 s floor.
    func admitPoll() -> Bool {
        let ok = scheduler.admit(at: Date())
        if !ok { arm() }
        return ok
    }

    /// Local `file://` addresses become their file name, or `[local file]` (spec 6.4).
    static func replaceLocalFiles(_ text: String) -> String {
        localFile.sub(text) { match in
            let name = URL(string: match.value)?.lastPathComponent ?? ""
            return name.isEmpty || name == "/" ? "[local file]" : name
        }
    }

    /// The snapshot outcome of a gate stop.
    static func outcome(_ result: GateResult) -> SnapshotOutcome {
        switch result {
        case .pass: return .captured
        case .withheld(let category): return .withheld(category)
        case .skip(let reason): return .skipped(reason)
        }
    }
}
