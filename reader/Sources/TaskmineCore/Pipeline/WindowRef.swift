import Foundation

/// A window a reader can snapshot: the owning process and the window's `window:<uuid>` reference.
public struct WindowRef: Hashable, Sendable {
    public let pid: Int32
    public let ref: String

    /// A window of process `pid`.
    public init(pid: Int32, ref: String) {
        self.pid = pid
        self.ref = ref
    }
}
