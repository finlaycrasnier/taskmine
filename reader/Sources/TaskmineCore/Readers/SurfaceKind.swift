import Foundation

/// How an app shows web content: not at all, a supported browser, Electron, or a browser never read.
public enum SurfaceKind: Sendable, Equatable {
    case native, chromium, gecko, electron, unsupported

    /// True for a browser whose pages are read.
    var isBrowser: Bool { self == .chromium || self == .gecko }
    /// True when the walk uses the web budgets.
    var isWeb: Bool { isBrowser || self == .electron }
}
