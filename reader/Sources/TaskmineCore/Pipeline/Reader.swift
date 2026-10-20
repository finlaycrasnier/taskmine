import Foundation

/// A source of readings: the text reader, the picture reader or an app reader.
public protocol Reader {
    /// The wire provenance of every reading this reader returns.
    var provenance: String { get }

    /// Starts observing; throws when a permission is missing.
    func start() throws

    /// Stops observing and releases what `start` took.
    func stop()

    /// One reading of a window, or nil when it shows nothing readable.
    func snapshot(of window: WindowRef) -> Reading?
}
