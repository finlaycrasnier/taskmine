import CryptoKit
import Foundation

// docs/CONTRACTS.md section 3 "Reader queue"; reader spec 8.5, 8.6.
/// The reader queue in reader.db: pending, acked or quarantined event revisions.
public final class Queue {
    private let db: SQLiteDatabase
    private static let openBatchKey = "open_batch"

    /// Opens or creates the queue file at `path`.
    public init(path: String) throws {
        db = try SQLiteDatabase(path: path)
        try db.execute("""
            PRAGMA busy_timeout=5000; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS queue (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', code TEXT, reason TEXT, created_at TEXT NOT NULL,
              UNIQUE(event_id, revision));
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS queue_state ON queue(state, sequence);
            CREATE INDEX IF NOT EXISTS queue_created ON queue(created_at);
            """)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path)
    }

    /// Adds one event revision as pending; the same revision again must be byte-equal.
    public func append(eventID: String, revision: Int, payload: Data, createdAt: Date) throws {
        guard let text = String(data: payload, encoding: .utf8) else { throw QueueError.invalidPayload }
        let inserted = try db.run(
            "INSERT OR IGNORE INTO queue (event_id, revision, payload, created_at) VALUES (?, ?, ?, ?)",
            [.text(eventID), .int(Int64(revision)), .text(text), .text(WireFormat.time(createdAt))])
        if inserted > 0 { return }
        let stored = try db.rows("SELECT payload FROM queue WHERE event_id = ? AND revision = ?",
                                 [.text(eventID), .int(Int64(revision))])
        guard stored.first?.first?.string.map({ PyText.same($0, text) }) == true else {
            throw QueueError.conflict(eventID: eventID, revision: revision)
        }
    }

    /// The number of pending rows.
    public var pendingCount: Int {
        let rows = try? db.rows("SELECT COUNT(*) FROM queue WHERE state = 'pending'")
        return Int(rows?.first?.first?.int ?? 0)
    }

    // Else: the newest pending revision of each event, oldest first, within `limit` and `maxBytes`.
    /// The batch to send; an open batch is sent again unchanged.
    public func nextBatch(limit: Int = 50, maxBytes: Int = 1_500_000) throws -> Batch? {
        if let open = try openBatch() { return open }
        let members = try candidates(limit: limit, maxBytes: maxBytes)
        if members.isEmpty { return nil }
        let keys = members.map { QueueMember(eventID: $0.eventID, revision: $0.revision) }
        let batch = Batch(batchID: Self.batchID(keys), events: members.map(\.payload))
        try saveOpen(batch.batchID, keys)
        return batch
    }

    /// Pending rows that fit one batch, in sequence order.
    private func candidates(limit: Int, maxBytes: Int) throws -> [(eventID: String, revision: Int, payload: Data)] {
        let rows = try db.rows("""
            SELECT q.event_id, q.revision, q.payload FROM queue q WHERE q.state = 'pending'
              AND q.revision = (SELECT MAX(p.revision) FROM queue p WHERE p.event_id = q.event_id AND p.state = 'pending')
            ORDER BY q.sequence LIMIT ?
            """, [.int(Int64(max(1, min(limit, 100))))])
        var out: [(eventID: String, revision: Int, payload: Data)] = []
        var bytes = 0
        for row in rows {
            let payload = Data((row[2].string ?? "").utf8)
            if !out.isEmpty && bytes + payload.count > maxBytes { break }
            bytes += payload.count
            out.append((row[0].string ?? "", Int(row[1].int ?? 0), payload))
        }
        return out
    }

    /// Marks the open batch from a valid ack: accepted rows acked, rejected rows quarantined.
    public func acknowledge(_ ack: Ack) throws {
        guard let (batchID, members) = try loadOpen(), batchID == ack.batchID else { throw QueueError.invalidAck }
        let named = ack.accepted.map { QueueMember(eventID: $0.eventID, revision: $0.revision) }
            + ack.rejected.map { QueueMember(eventID: $0.eventID, revision: $0.revision) }
        guard named.count == members.count, Set(named) == Set(members), Set(named).count == named.count else {
            throw QueueError.invalidAck
        }
        try db.transaction {
            for item in ack.accepted {
                try db.run("UPDATE queue SET state = 'acked' WHERE event_id = ? AND revision <= ? AND state = 'pending'",
                           [.text(item.eventID), .int(Int64(item.revision))])
            }
            for item in ack.rejected {
                try db.run("""
                    UPDATE queue SET state = 'quarantined', code = ?, reason = ?
                    WHERE event_id = ? AND revision <= ? AND state = 'pending'
                    """, [.text(item.code), item.reason.map { .text(String($0.unicodeScalars.prefix(200))) } ?? .null,
                          .text(item.eventID), .int(Int64(item.revision))])
            }
            try db.run("DELETE FROM settings WHERE key = ?", [.text(Self.openBatchKey)])
        }
    }

    /// Deletes rows created before `date`, in any state; returns how many it deleted.
    public func prune(olderThan date: Date) throws -> Int {
        let cutoff = WireFormat.time(Date(timeIntervalSince1970: date.timeIntervalSince1970.rounded(.up)))
        return try db.run("DELETE FROM queue WHERE created_at < ?", [.text(cutoff)])
    }

    /// The open batch rebuilt from its members, or nil (and forgotten) when a member is gone.
    private func openBatch() throws -> Batch? {
        guard let (batchID, members) = try loadOpen() else { return nil }
        var events: [Data] = []
        for member in members {
            let rows = try db.rows("SELECT payload FROM queue WHERE event_id = ? AND revision = ? AND state = 'pending'",
                                   [.text(member.eventID), .int(Int64(member.revision))])
            guard let payload = rows.first?.first?.string else {
                try db.run("DELETE FROM settings WHERE key = ?", [.text(Self.openBatchKey)])
                return nil
            }
            events.append(Data(payload.utf8))
        }
        return Batch(batchID: batchID, events: events)
    }

    /// The stored open batch id and members.
    private func loadOpen() throws -> (String, [QueueMember])? {
        let rows = try db.rows("SELECT value FROM settings WHERE key = ?", [.text(Self.openBatchKey)])
        guard let text = rows.first?.first?.string,
              let open = try? JSONDecoder().decode(OpenBatch.self, from: Data(text.utf8)) else { return nil }
        return (open.batchID, open.members)
    }

    /// Remembers the open batch so a retry or a reopened file sends it unchanged.
    private func saveOpen(_ batchID: String, _ members: [QueueMember]) throws {
        let data = try JSONEncoder().encode(OpenBatch(batchID: batchID, members: members))
        try db.run("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                   [.text(Self.openBatchKey), .text(String(decoding: data, as: UTF8.self))])
    }

    /// A batch id derived from the member ids and revisions.
    static func batchID(_ members: [QueueMember]) -> String {
        let text = members.map { "\($0.eventID)\u{1f}\($0.revision)" }.joined(separator: "\n")
        let hex = SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
        return "batch-" + hex.prefix(32)
    }
}
