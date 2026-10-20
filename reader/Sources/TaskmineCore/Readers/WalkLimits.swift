import Foundation

/// Bounds of a text walk (reader spec 2.5).
struct WalkLimits: Sendable {
    let maxDepth: Int
    let maxNodes: Int
    let maxCells: Int
    /// Most scalars kept in the stored text; the walk collects 1.5 times this.
    let storedLimit: Int
    let seconds: TimeInterval
    /// Web content: extra leaf roles may report no children.
    let isWeb: Bool

    /// A native window.
    static let native = WalkLimits(maxDepth: 8, maxNodes: 500, maxCells: 3_000, storedLimit: 16_384, seconds: 0.75,
                                   isWeb: false)
    /// A browser page or an Electron window.
    static let web = WalkLimits(maxDepth: 48, maxNodes: 3_000, maxCells: 3_000, storedLimit: 60_000, seconds: 0.75,
                                isWeb: true)
}
