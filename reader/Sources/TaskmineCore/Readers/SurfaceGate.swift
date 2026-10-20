import AppKit
import ApplicationServices
import Foundation

/// The checks before any text is read, in the order of reader spec 2.3 and 4.
@MainActor final class SurfaceGate {
    private static let domainPattern = Rx("^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\\z")
    private static let loopback: Set<String> = ["localhost", "127.0.0.1", "::1", "[::1]"]

    /// The policy from the last cycle; nil blocks everything.
    var policy: Policy?
    let identity = WindowIdentity()
    let wake: WebWake
    /// Set when a web-tree wake changed something; the reader snapshots again 1 s later.
    var wokeAt: Date?
    private let corePort: Int
    private let ownPID = ProcessInfo.processInfo.processIdentifier
    private let extra = WebSurfaces.extra

    /// A gate for a core on `corePort`, journaling wakes to `wakeJournal`.
    init(corePort: Int, wakeJournal: URL) {
        self.corePort = corePort
        wake = WebWake(journal: wakeJournal)
    }

    /// Steps 1 to 4: session, permission, policy, app blocklist; nil when the app may be inspected.
    func appCheck(_ app: AppInfo, provenance: String) -> GateResult? {
        guard MacSession.isAvailable() else { return .skip(nil) }
        guard MacSession.accessibilityGranted else { return .skip(.permissionUnavailable) }
        guard let policy, policy.state == .active, policy.allows(app: app.bundleID),
              policy.allowedProvenance.contains(provenance) else { return .skip(.sourceDisabled) }
        let id = app.bundleID.lowercased()
        if id.isEmpty || id == "unknown.bundle" || app.pid == ownPID { return .skip(nil) }
        if extra.isDenied(id) { return .skip(.protectedApplication) }
        if let reason = Surfaces.appReason(id) { return .withheld(Self.category(reason)) }
        if app.kind == .unsupported { return .skip(.browserRequiresExtension) }
        return nil
    }

    /// Steps 1 to 12 for the app's focused window.
    func check(_ app: AppInfo, provenance: String, budget: InspectionBudget = .standard) -> GateResult {
        if let stop = appCheck(app, provenance: provenance) { return stop }
        let appElement = AX.application(app.pid)
        if wake.wake(pid: app.pid, kind: app.kind, bundleID: app.bundleID) { wokeAt = Date() }
        guard let window = AX.element(appElement, "AXFocusedWindow") else { return .skip(.secureOrUninspectableSurface) }
        if let focused = AX.element(appElement, "AXFocusedUIElement"),
           AX.isSecure(AX.string(focused, "AXRole"), AX.string(focused, "AXSubrole")) {
            return .withheld("secure_surface")
        }
        let title = AX.string(window, "AXTitle") ?? ""
        let result = app.kind.isWeb ? webCheck(app, window, title)
            : nativeCheck(app, window, title, AX.element(appElement, "AXFocusedUIElement"), budget)
        guard case .pass = result else { return result }
        return NSWorkspace.shared.frontmostApplication?.processIdentifier == app.pid ? result : .skip(nil)
    }

    /// A native window: inspection with fallback, title, document address.
    private func nativeCheck(_ app: AppInfo, _ window: AXUIElement, _ title: String, _ focused: AXUIElement?,
                             _ budget: InspectionBudget) -> GateResult {
        let inspector = inspector(for: app)
        var root = window
        if let failure = inspector.inspect(window, budget: budget) {
            guard failure == .nodeBudget || failure == .depthLimit else { return .withheld(failure.withheldCategory) }
            switch inspector.fallbackRoot(window: window, focused: focused, budget: budget) {
            case .success(let area): root = area
            case .failure(let failure): return .withheld(failure.withheldCategory)
            }
        }
        if TextSafety.evaluate(title, mode: .title).excluded { return .withheld("credential_surface") }
        let url = AX.url(window, "AXDocument") ?? AX.url(window, "AXURL")
        var documentID: String?
        if let url {
            if url.isFileURL {
                guard WindowIdentity.isSafeFile(url) else { return .withheld("credential_surface") }
                documentID = identity.documentID(url)
            } else if !extra.allowsEditorPage(url, bundleID: app.bundleID, appPath: app.bundlePath) {
                return .skip(.secureOrUninspectableSurface)
            }
        }
        return .pass(surface(app, window, title, root: root, webArea: nil, url: url, domain: nil, documentID))
    }

    /// A browser or Electron window: native scan, title, private window, page address checks.
    private func webCheck(_ app: AppInfo, _ window: AXUIElement, _ title: String) -> GateResult {
        let scan = BrowserScan.run(window)
        if scan.failed { return .withheld("secure_surface") }
        if TextSafety.evaluate(title, mode: .title).excluded { return .withheld("credential_surface") }
        guard app.kind.isBrowser else {
            return .pass(surface(app, window, title, root: window, webArea: nil, url: nil, domain: nil, nil))
        }
        if Surfaces.isPrivateWindow(title: title, labels: scan.labels) { return .withheld("private_browsing") }
        guard let page = scan.page() else { return .skip(.noVisibleContext) }
        if page.url.isFileURL {
            guard WindowIdentity.isSafeFile(page.url) else { return .withheld("credential_surface") }
            return .pass(surface(app, window, title, root: page.area, webArea: page.area, url: page.url,
                                 domain: "local-file", identity.documentID(page.url)))
        }
        switch pageCheck(page.url) {
        case .success(let domain):
            return .pass(surface(app, window, title, root: page.area, webArea: page.area, url: page.url,
                                 domain: domain, nil))
        case .failure(let stop): return stop.result
        }
    }

    /// A web address: URL blocklist, sanitizer, host shape, excluded domains, own origin (spec 3.5, 4).
    func pageCheck(_ url: URL) -> Result<String, GateStop> {
        if let reason = Surfaces.urlReason(url.absoluteString) { return .failure(.withheld(Self.category(reason))) }
        guard TextSafety.sanitizeURL(url.absoluteString) != nil else { return .failure(.withheld("credential_surface")) }
        let host = (url.host ?? "").lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        if isOwnOrigin(url, host: host) { return .failure(.skip(nil)) }
        guard host.unicodeScalars.count <= 253, Self.domainPattern.match(host as NSString) != nil else {
            return .failure(.skip(.noVisibleContext))
        }
        if policy?.excludes(host: host) ?? true { return .failure(.withheld("excluded_site")) }
        return .success(host)
    }

    /// Checks a web area inside an Electron window during the walk; a withheld category blocks it.
    func electronAreaCheck(_ area: AXUIElement) -> String? {
        guard let url = BrowserScan.url(of: area), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https" else { return nil }
        switch pageCheck(url) {
        case .success: return nil
        case .failure(let stop):
            if case .withheld(let category) = stop.result { return category }
            return "excluded_site"
        }
    }

    /// True when the address is the core's own page; loopback aliases match on scheme and port.
    private func isOwnOrigin(_ url: URL, host: String) -> Bool {
        let port = url.port ?? (url.scheme?.lowercased() == "https" ? 443 : 80)
        return url.scheme?.lowercased() == "http" && Self.loopback.contains(host) && port == corePort
    }

    /// The inspector for an app: web areas only in the editor renderer exception.
    func inspector(for app: AppInfo) -> PrivacyInspector {
        let extra = self.extra
        return PrivacyInspector { area in
            app.kind.isWeb || extra.allowsEditorPage(BrowserScan.url(of: area), bundleID: app.bundleID,
                                                     appPath: app.bundlePath)
        }
    }

    /// Builds the surface.
    private func surface(_ app: AppInfo, _ window: AXUIElement, _ title: String, root: AXUIElement,
                         webArea: AXUIElement?, url: URL?, domain: String?, _ documentID: String?) -> Surface {
        Surface(app: app, window: window, windowRef: identity.ref(pid: app.pid, launch: app.launch, window: window),
                title: title, webArea: webArea, url: url, domain: domain, documentID: documentID, walkRoot: root,
                policyVersion: policy?.version ?? 0)
    }

    /// The withheld category of a blocklist reason.
    static func category(_ reason: String) -> String {
        switch reason {
        case "authentication_dialog": return "authentication"
        case "sensitive_domain": return "sensitive_site"
        default: return reason
        }
    }
}
