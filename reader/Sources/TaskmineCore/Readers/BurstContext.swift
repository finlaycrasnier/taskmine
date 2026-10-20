import ApplicationServices
import Foundation

/// What an open edit burst remembers: the element, its app, policy version, domain and the first reading.
struct BurstContext {
    let element: AXUIElement
    let app: AppInfo
    let policyVersion: Int
    /// `browser_domain` when the burst started (or nil for a native window).
    let domain: String?
    /// The `edit` reading written when the burst started.
    let base: Reading
}
