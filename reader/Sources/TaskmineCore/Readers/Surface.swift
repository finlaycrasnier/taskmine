import ApplicationServices
import Foundation

/// A window that passed every check before its text is read (reader spec 2.3 steps 1 to 12).
struct Surface {
    let app: AppInfo
    let window: AXUIElement
    let windowRef: String
    /// The raw title, used in memory and saved only after the pipeline.
    let title: String
    /// The browser page's web area, the walk root for a browser.
    let webArea: AXUIElement?
    /// The page or document address, in memory only.
    let url: URL?
    /// `browser_domain`: host only, or `local-file`.
    let domain: String?
    let documentID: String?
    /// Where the walk starts: the window, a content area or the web area.
    let walkRoot: AXUIElement
    let policyVersion: Int

    /// The metadata every reading of this surface carries.
    var metadata: [String: String] {
        var out = ["app_bundle_id": app.bundleID, "app_name": app.name, "window_title": title, "window_ref": windowRef]
        if let domain { out["browser_domain"] = domain }
        if let documentID { out["document_id"] = documentID }
        return out
    }
}
