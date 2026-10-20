import Foundation

/// Why a queue operation failed.
public enum QueueError: Error, Equatable {
    /// The ack names another batch, or does not name every event of the batch exactly once.
    case invalidAck
    /// The same event revision was appended twice with different bytes.
    case conflict(eventID: String, revision: Int)
    /// The payload is not UTF-8 JSON text.
    case invalidPayload
    /// SQLite reported an error.
    case sqlite(message: String)
}
