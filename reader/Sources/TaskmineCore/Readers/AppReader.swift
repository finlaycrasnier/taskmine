import AppKit
import Foundation

/// An app reader entry returns the app's own data as a Reading.
public typealias AppReaderProvider = @MainActor (WindowRef) -> Reading?

/// Readers that ask an app for its data, keyed by bundle ID; consulted before the text reader.
@MainActor public final class AppReader: @MainActor Reader {
    public let provenance = "app_reader"
    private var providers: [String: AppReaderProvider] = [:]

    /// An app reader with no entries.
    public init() {}

    /// Adds or replaces the entry for a bundle ID.
    public func register(bundleID: String, provider: @escaping AppReaderProvider) {
        providers[bundleID.lowercased()] = provider
    }

    /// True when an entry exists for the bundle ID.
    public func handles(bundleID: String) -> Bool { providers[bundleID.lowercased()] != nil }

    /// Nothing to start.
    public func start() throws {}

    /// Nothing to stop.
    public func stop() {}

    /// The entry's reading for the window's app, with this reader's provenance.
    public func snapshot(of window: WindowRef) -> Reading? {
        guard let bundleID = NSRunningApplication(processIdentifier: window.pid)?.bundleIdentifier,
              let provider = providers[bundleID.lowercased()], var reading = provider(window) else { return nil }
        reading.provenance = provenance
        reading.metadata["app_bundle_id"] = bundleID
        reading.metadata["window_ref"] = window.ref
        return reading
    }
}
