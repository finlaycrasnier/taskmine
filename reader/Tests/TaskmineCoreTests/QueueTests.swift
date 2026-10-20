// The reader queue (docs/CONTRACTS.md section 3 "Reader queue"; reader spec 8.5, 8.6).
//
// Choices the implementation must meet:
// - The file at `path` holds the CONTRACTS table `queue(sequence, event_id, revision,
//   payload, state, code, reason, created_at)`; these tests read `state`, `code` and
//   `reason` from it with SQLite to check acknowledgement.
// - nextBatch picks the newest pending revision of each event, ordered by the `sequence`
//   of the picked row, at most `limit` events and at most `maxBytes` payload bytes (one
//   event always fits). `events` are the stored payload bytes, unchanged.
// - A batch not yet acknowledged is sent again unchanged (same id, same events), also
//   after more appends and after reopening the file. Its id is derived from the member
//   ids, so two queues with the same rows give the same id.
// - acknowledge marks accepted rows `acked` and rejected rows `quarantined` with their
//   code and reason. A row rejected with credential_detected keeps an empty payload (a
//   stub: id, revision, time, code and reason); other rejection codes keep the payload.
//   An ack with another batch id, or that does not name every event of the batch exactly
//   once, throws QueueError.invalidAck and changes nothing.
// - pendingCount counts pending rows. prune(olderThan:) deletes rows whose createdAt is
//   before the date, in any state, and returns how many it deleted.
import CryptoKit
import Foundation
import SQLite3
import Testing
import TaskmineCore

private let now = Date(timeIntervalSince1970: 1_790_000_000)

private func iso(_ date: Date) -> String {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f.string(from: date)
}

/// A valid taskmine.event/1 JSON payload.
private func payload(_ id: String, revision: Int = 1, text: String = "Approval is required.") -> Data {
    let hash = SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    let event: [String: Any] = [
        "schema": "taskmine.event/1",
        "event_id": id,
        "revision": revision,
        "source_id": "mac-fixture",
        "observed_at": iso(now),
        "first_seen_at": iso(now),
        "last_seen_at": iso(now),
        "provenance": "synthetic",
        "content": [
            "type": "screen_text",
            "text": text,
            "text_sha256": hash,
            "metadata": ["app_bundle_id": "com.apple.TextEdit", "app_name": "TextEdit"],
        ] as [String: Any],
    ]
    return try! JSONSerialization.data(withJSONObject: event, options: [.sortedKeys])
}

private func member(_ data: Data) -> (id: String, revision: Int) {
    let dict = try! JSONSerialization.jsonObject(with: data) as! [String: Any]
    return (dict["event_id"] as! String, dict["revision"] as! Int)
}

private struct AckItem {
    let id: String
    let revision: Int
    let code: String?   // nil: accepted as "stored"
}

private func ack(_ batchID: String, _ items: [AckItem]) throws -> Ack {
    let accepted = items.filter { $0.code == nil }
        .map { ["event_id": $0.id, "revision": $0.revision, "status": "stored"] as [String: Any] }
    let rejected = items.filter { $0.code != nil }
        .map { ["event_id": $0.id, "revision": $0.revision, "code": $0.code!, "reason": "fixture reason"] as [String: Any] }
    let body: [String: Any] = [
        "schema": "taskmine.ack/1", "batch_id": batchID, "accepted": accepted, "rejected": rejected,
    ]
    return try JSONDecoder().decode(Ack.self, from: JSONSerialization.data(withJSONObject: body))
}

private func accept(_ batch: Batch) -> [AckItem] {
    batch.events.map { let m = member($0); return AckItem(id: m.id, revision: m.revision, code: nil) }
}

private final class TempDir {
    let url: URL
    init() {
        url = FileManager.default.temporaryDirectory
            .appendingPathComponent("taskmine-queue-\(UUID().uuidString)")
        try! FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    }
    var dbPath: String { url.appendingPathComponent("reader.db").path }
    deinit { try? FileManager.default.removeItem(at: url) }
}

/// Reads the payload of one row straight from the queue table.
private func storedPayload(_ path: String, _ id: String, _ revision: Int) -> String? {
    var db: OpaquePointer?
    guard sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else { return nil }
    defer { sqlite3_close(db) }
    var stmt: OpaquePointer?
    guard sqlite3_prepare_v2(db, "SELECT payload FROM queue WHERE event_id = ? AND revision = ?", -1, &stmt, nil)
        == SQLITE_OK else { return nil }
    defer { sqlite3_finalize(stmt) }
    let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    sqlite3_bind_text(stmt, 1, id, -1, transient)
    sqlite3_bind_int64(stmt, 2, Int64(revision))
    guard sqlite3_step(stmt) == SQLITE_ROW, let p = sqlite3_column_text(stmt, 0) else { return nil }
    return String(cString: p)
}

/// Reads (state, code, reason) of one row straight from the queue table.
private func row(_ path: String, _ id: String, _ revision: Int) -> (state: String, code: String?, reason: String?)? {
    var db: OpaquePointer?
    guard sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else { return nil }
    defer { sqlite3_close(db) }
    var stmt: OpaquePointer?
    let sql = "SELECT state, code, reason FROM queue WHERE event_id = ? AND revision = ?"
    guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }
    defer { sqlite3_finalize(stmt) }
    let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    sqlite3_bind_text(stmt, 1, id, -1, transient)
    sqlite3_bind_int64(stmt, 2, Int64(revision))
    guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
    func text(_ i: Int32) -> String? {
        guard let p = sqlite3_column_text(stmt, i) else { return nil }
        return String(cString: p)
    }
    return (text(0) ?? "", text(1), text(2))
}

private func expectInvalidAck(_ body: () throws -> Void) {
    do {
        try body()
        Issue.record("ack accepted")
    } catch {
        guard case QueueError.invalidAck = error else {
            Issue.record("expected QueueError.invalidAck, got \(error)")
            return
        }
    }
}

@Suite struct QueueTests {
    @Test func appendCountsPending() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        #expect(queue.pendingCount == 0)
        #expect(try queue.nextBatch() == nil)
        for i in 0..<3 {
            try queue.append(eventID: "desktop-\(i)", revision: 1, payload: payload("desktop-\(i)"), createdAt: now)
        }
        #expect(queue.pendingCount == 3)
        #expect(row(dir.dbPath, "desktop-1", 1)?.state == "pending")
    }

    @Test func batchOfFiftyOldestFirst() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        var payloads: [Data] = []
        for i in 0..<120 {
            let id = String(format: "desktop-%03d", i)
            payloads.append(payload(id))
            try queue.append(eventID: id, revision: 1, payload: payloads[i], createdAt: now)
        }
        let batch = try #require(try queue.nextBatch())
        #expect(batch.events.count == 50)
        #expect(batch.events == Array(payloads[0..<50]))
    }

    @Test func limitParameter() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        for i in 0..<20 {
            try queue.append(eventID: "desktop-\(i)", revision: 1, payload: payload("desktop-\(i)"), createdAt: now)
        }
        let batch = try #require(try queue.nextBatch(limit: 10))
        #expect(batch.events.count == 10)
        #expect(batch.events.map { member($0).id } == (0..<10).map { "desktop-\($0)" })
    }

    @Test func newestRevisionOnly() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        let a1 = payload("desktop-a", revision: 1, text: "one")
        let b1 = payload("desktop-b", revision: 1, text: "two")
        let a2 = payload("desktop-a", revision: 2, text: "one more")
        try queue.append(eventID: "desktop-a", revision: 1, payload: a1, createdAt: now)
        try queue.append(eventID: "desktop-b", revision: 1, payload: b1, createdAt: now)
        try queue.append(eventID: "desktop-a", revision: 2, payload: a2, createdAt: now)
        let batch = try #require(try queue.nextBatch())
        // Ordered by the sequence of the picked row: b (row 2) before a revision 2 (row 3).
        #expect(batch.events == [b1, a2])
    }

    @Test func byteCap() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        let text = String(repeating: "x", count: 1_000)
        let payloads = (0..<5).map { payload("desktop-\($0)", text: text) }
        for (i, p) in payloads.enumerated() {
            try queue.append(eventID: "desktop-\(i)", revision: 1, payload: p, createdAt: now)
        }
        let size = payloads[0].count
        #expect(payloads.allSatisfy { $0.count == size })
        let batch = try #require(try queue.nextBatch(maxBytes: size * 2 + size / 2))
        #expect(batch.events == Array(payloads[0..<2]))
    }

    @Test func oneEventAlwaysFits() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        let big = payload("desktop-big", text: String(repeating: "x", count: 5_000))
        try queue.append(eventID: "desktop-big", revision: 1, payload: big, createdAt: now)
        try queue.append(eventID: "desktop-next", revision: 1, payload: payload("desktop-next"), createdAt: now)
        let batch = try #require(try queue.nextBatch(maxBytes: 1_000))
        #expect(batch.events == [big])
    }

    @Test func sameBatchOnRetry() throws {
        let dir = TempDir()
        var queue = try Queue(path: dir.dbPath)
        for i in 0..<3 {
            try queue.append(eventID: "desktop-\(i)", revision: 1, payload: payload("desktop-\(i)"), createdAt: now)
        }
        let first = try #require(try queue.nextBatch())
        let again = try #require(try queue.nextBatch())
        #expect(again.batchID == first.batchID)
        #expect(again.events == first.events)

        // More rows do not change a batch that waits for its ack.
        try queue.append(eventID: "desktop-9", revision: 1, payload: payload("desktop-9"), createdAt: now)
        let afterAppend = try #require(try queue.nextBatch())
        #expect(afterAppend.batchID == first.batchID)
        #expect(afterAppend.events == first.events)

        // Nor does reopening the file.
        queue = try Queue(path: dir.dbPath)
        let reopened = try #require(try queue.nextBatch())
        #expect(reopened.batchID == first.batchID)
        #expect(reopened.events == first.events)
    }

    @Test func batchIDDerivesFromMembers() throws {
        let dirs = [TempDir(), TempDir(), TempDir()]
        var used = 0
        func firstBatch(_ ids: [String]) throws -> Batch? {
            defer { used += 1 }
            let queue = try Queue(path: dirs[used].dbPath)
            for id in ids { try queue.append(eventID: id, revision: 1, payload: payload(id), createdAt: now) }
            return try queue.nextBatch()
        }
        let a = try #require(try firstBatch(["desktop-1", "desktop-2"]))
        let b = try #require(try firstBatch(["desktop-1", "desktop-2"]))
        let c = try #require(try firstBatch(["desktop-1", "desktop-3"]))
        #expect(a.batchID == b.batchID)
        #expect(a.batchID != c.batchID)
        let pattern = try Regex("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
        #expect(a.batchID.wholeMatch(of: pattern) != nil)
    }

    @Test func acknowledgeMarksAckedAndQuarantined() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        for i in 0..<3 {
            try queue.append(eventID: "desktop-\(i)", revision: 1, payload: payload("desktop-\(i)"), createdAt: now)
        }
        let batch = try #require(try queue.nextBatch())
        try queue.acknowledge(try ack(batch.batchID, [
            AckItem(id: "desktop-0", revision: 1, code: nil),
            AckItem(id: "desktop-1", revision: 1, code: "credential_detected"),
            AckItem(id: "desktop-2", revision: 1, code: nil),
        ]))
        #expect(row(dir.dbPath, "desktop-0", 1)?.state == "acked")
        #expect(row(dir.dbPath, "desktop-2", 1)?.state == "acked")
        let quarantined = try #require(row(dir.dbPath, "desktop-1", 1))
        #expect(quarantined.state == "quarantined")
        #expect(quarantined.code == "credential_detected")
        #expect(quarantined.reason == "fixture reason")
        #expect(queue.pendingCount == 0)
        // Acked and quarantined rows are never sent again.
        #expect(try queue.nextBatch() == nil)
    }

    @Test func credentialRejectionKeepsStubOnly() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        try queue.append(eventID: "desktop-0", revision: 1, payload: payload("desktop-0"), createdAt: now)
        try queue.append(eventID: "desktop-0", revision: 2, payload: payload("desktop-0", revision: 2), createdAt: now)
        let batch = try #require(try queue.nextBatch())
        try queue.acknowledge(try ack(batch.batchID, [AckItem(id: "desktop-0", revision: 2, code: "credential_detected")]))
        // Both revisions become stubs: state, code and reason stay; the payload is gone.
        for revision in [1, 2] {
            let stub = try #require(row(dir.dbPath, "desktop-0", revision))
            #expect(stub.state == "quarantined")
            #expect(stub.code == "credential_detected")
            #expect(stub.reason == "fixture reason")
            #expect(storedPayload(dir.dbPath, "desktop-0", revision) == "")
        }
    }

    @Test func otherRejectionsKeepPayload() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        let codes = ["invalid_event", "policy_denied", "event_expired"]
        for i in codes.indices {
            try queue.append(eventID: "desktop-\(i)", revision: 1, payload: payload("desktop-\(i)"), createdAt: now)
        }
        let batch = try #require(try queue.nextBatch())
        try queue.acknowledge(try ack(batch.batchID, codes.indices.map {
            AckItem(id: "desktop-\($0)", revision: 1, code: codes[$0])
        }))
        for i in codes.indices {
            #expect(row(dir.dbPath, "desktop-\(i)", 1)?.code == codes[i])
            let kept = try #require(storedPayload(dir.dbPath, "desktop-\(i)", 1))
            #expect(Data(kept.utf8) == payload("desktop-\(i)"))
        }
    }

    @Test func pruneRemovesQuarantinedRows() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        let old = now.addingTimeInterval(-31 * 86_400)
        try queue.append(eventID: "desktop-a", revision: 1, payload: payload("desktop-a"), createdAt: old)
        try queue.append(eventID: "desktop-b", revision: 1, payload: payload("desktop-b"), createdAt: old)
        let batch = try #require(try queue.nextBatch())
        try queue.acknowledge(try ack(batch.batchID, [
            AckItem(id: "desktop-a", revision: 1, code: "credential_detected"),
            AckItem(id: "desktop-b", revision: 1, code: "invalid_event"),
        ]))
        #expect(try queue.prune(olderThan: now.addingTimeInterval(-30 * 86_400)) == 2)
        #expect(row(dir.dbPath, "desktop-a", 1) == nil)
        #expect(row(dir.dbPath, "desktop-b", 1) == nil)
    }

    @Test func nextBatchAfterAckTakesNewRows() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        try queue.append(eventID: "desktop-0", revision: 1, payload: payload("desktop-0"), createdAt: now)
        let first = try #require(try queue.nextBatch())
        try queue.append(eventID: "desktop-1", revision: 1, payload: payload("desktop-1"), createdAt: now)
        try queue.acknowledge(try ack(first.batchID, accept(first)))
        let second = try #require(try queue.nextBatch())
        #expect(second.batchID != first.batchID)
        #expect(second.events.map { member($0).id } == ["desktop-1"])
    }

    @Test func invalidAckThrows() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        for i in 0..<3 {
            try queue.append(eventID: "desktop-\(i)", revision: 1, payload: payload("desktop-\(i)"), createdAt: now)
        }
        let batch = try #require(try queue.nextBatch())
        let all = accept(batch)

        // One event missing.
        let missing = try ack(batch.batchID, Array(all.dropLast()))
        expectInvalidAck { try queue.acknowledge(missing) }
        // One event named twice (accepted and rejected).
        let twice = try ack(batch.batchID, all + [AckItem(id: "desktop-0", revision: 1, code: "invalid_event")])
        expectInvalidAck { try queue.acknowledge(twice) }
        // An event that is not in the batch.
        let extra = try ack(batch.batchID, all + [AckItem(id: "desktop-x", revision: 1, code: nil)])
        expectInvalidAck { try queue.acknowledge(extra) }
        // The right events under another batch id.
        let otherID = try ack("other-batch", all)
        expectInvalidAck { try queue.acknowledge(otherID) }
        // A wrong revision.
        var wrongRevision = all
        wrongRevision[0] = AckItem(id: all[0].id, revision: 2, code: nil)
        let badRevision = try ack(batch.batchID, wrongRevision)
        expectInvalidAck { try queue.acknowledge(badRevision) }

        // Nothing changed.
        #expect(queue.pendingCount == 3)
        #expect(row(dir.dbPath, "desktop-0", 1)?.state == "pending")
        let retry = try #require(try queue.nextBatch())
        #expect(retry.batchID == batch.batchID)
        #expect(retry.events == batch.events)

        // The valid ack still works afterwards.
        try queue.acknowledge(try ack(batch.batchID, all))
        #expect(queue.pendingCount == 0)
    }

    @Test func prune() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        let old = now.addingTimeInterval(-40 * 86_400)
        for i in 0..<3 {
            try queue.append(eventID: "desktop-old-\(i)", revision: 1, payload: payload("desktop-old-\(i)"), createdAt: old)
        }
        for i in 0..<2 {
            try queue.append(eventID: "desktop-new-\(i)", revision: 1, payload: payload("desktop-new-\(i)"), createdAt: now)
        }
        let cutoff = now.addingTimeInterval(-30 * 86_400)
        #expect(try queue.prune(olderThan: cutoff) == 3)
        #expect(queue.pendingCount == 2)
        #expect(row(dir.dbPath, "desktop-old-0", 1) == nil)
        #expect(try queue.prune(olderThan: cutoff) == 0)
    }

    @Test func pruneRemovesAckedRows() throws {
        let dir = TempDir()
        let queue = try Queue(path: dir.dbPath)
        let old = now.addingTimeInterval(-40 * 86_400)
        try queue.append(eventID: "desktop-old", revision: 1, payload: payload("desktop-old"), createdAt: old)
        let batch = try #require(try queue.nextBatch())
        try queue.acknowledge(try ack(batch.batchID, accept(batch)))
        // Acked rows stay until retention ends.
        #expect(row(dir.dbPath, "desktop-old", 1)?.state == "acked")
        #expect(try queue.prune(olderThan: now.addingTimeInterval(-30 * 86_400)) == 1)
        #expect(row(dir.dbPath, "desktop-old", 1) == nil)
    }
}
