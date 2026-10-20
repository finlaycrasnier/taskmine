import AppKit
import ApplicationServices
import Foundation

/// The final field value rule: read the value once when a burst ends and keep it only if every check holds.
@MainActor struct FinalValueCheck {
    /// Most scalars of a kept value.
    static let limit = 60_000

    let gate: SurfaceGate

    /// The value to keep and the `value_omitted` code; both nil when nothing is kept.
    func value(for burst: BurstContext) -> (text: String?, omitted: String?) {
        guard passes(burst), let raw = AX.string(burst.element, "AXValue"), !raw.isEmpty else { return (nil, nil) }
        if raw.unicodeScalars.count > Self.limit { return (nil, "too_long") }
        guard let hidden = TextSafety.evaluate(raw, mode: .text, limit: Self.limit).text, !hidden.isEmpty else {
            return (nil, nil)
        }
        return (hidden, nil)
    }

    /// Checks 1 to 6 of reader spec 2.7.
    private func passes(_ burst: BurstContext) -> Bool {
        guard !MacSession.secureInputOn(), MacSession.isAvailable(), let policy = gate.policy,
              policy.state == .active, policy.allowedProvenance.contains("native_interaction"),
              policy.version == burst.policyVersion, policy.allows(app: burst.app.bundleID) else { return false }
        let element = burst.element
        guard let pid = AX.pid(element), pid == burst.app.pid,
              NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == burst.app.bundleID,
              Surfaces.appReason(burst.app.bundleID) == nil else { return false }
        let role = AX.string(element, "AXRole"), subrole = AX.string(element, "AXSubrole")
        let fields = WebSurfaces.shared.formFieldRoles
        guard !AX.isSecure(role, subrole), fields.contains(role ?? "") || fields.contains(subrole ?? ""),
              let window = AX.element(element, "AXWindow") else { return false }
        let title = AX.string(window, "AXTitle") ?? ""
        guard !TextSafety.evaluate(title, mode: .title).excluded else { return false }
        return burst.app.kind.isWeb ? webPasses(burst, window, title) : nativePasses(burst, window)
    }

    /// A native window passes the privacy inspection again, 0.5 s deadline.
    private func nativePasses(_ burst: BurstContext, _ window: AXUIElement) -> Bool {
        let inspector = gate.inspector(for: burst.app)
        guard let failure = inspector.inspect(window, budget: .finalValue) else { return true }
        guard failure == .nodeBudget || failure == .depthLimit else { return false }
        if case .success = inspector.fallbackRoot(window: window, focused: burst.element, budget: .finalValue) {
            return true
        }
        return false
    }

    /// A browser or Electron window: not private, every page address allowed, same site as the burst.
    private func webPasses(_ burst: BurstContext, _ window: AXUIElement, _ title: String) -> Bool {
        let scan = BrowserScan.run(window)
        guard !scan.failed, !Surfaces.isPrivateWindow(title: title, labels: scan.labels) else { return false }
        for area in scan.webAreas {
            guard let url = BrowserScan.url(of: area), ["http", "https"].contains(url.scheme?.lowercased() ?? "") else {
                continue
            }
            guard case .success = gate.pageCheck(url) else { return false }
        }
        guard burst.app.kind.isBrowser else { return true }
        guard let page = scan.page() else { return false }
        let domain = page.url.isFileURL ? "local-file" : (page.url.host ?? "").lowercased()
            .trimmingCharacters(in: CharacterSet(charactersIn: "."))
        return domain == burst.domain
    }
}
