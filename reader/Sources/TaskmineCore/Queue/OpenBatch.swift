import Foundation

/// The batch waiting for its ack, stored in the settings table.
struct OpenBatch: Codable {
    let batchID: String
    let members: [QueueMember]
}
