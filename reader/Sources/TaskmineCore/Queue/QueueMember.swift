import Foundation

/// One event revision in a batch.
struct QueueMember: Codable, Hashable {
    let eventID: String
    let revision: Int
}
