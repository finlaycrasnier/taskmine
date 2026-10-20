import Foundation

/// What a text walk read: lines in reading order, scalar counts, gaps and any block.
struct WalkResult {
    var lines: [String] = []
    /// Scalars of kept text.
    var contentScalars = 0
    /// Scalars of interface furniture, counted and not kept.
    var furnitureScalars = 0
    /// The walk hit a node, depth or text limit.
    var truncated = false
    /// Fixed gap codes: accessibility-tree-truncated, accessibility-timeout, children-unavailable.
    var gaps: Set<String> = []
    /// Set when a secure field or a blocked web address stopped the walk: secure_field or a withheld category.
    var blocked: String?

    /// True when nothing stopped the walk early.
    var isComplete: Bool { !truncated && gaps.isEmpty && blocked == nil }
}
