// Wire contract (docs/CONTRACTS.md section 2): event, batch, ack, policy.
// Oracle: Fixtures/contract_fixtures/*.json.
//
// Choices the implementation must meet:
// - Event, Ack and Policy round-trip with a plain JSONDecoder() and JSONEncoder(); each type
//   owns its date and key format (snake_case keys, dates as "YYYY-MM-DDTHH:MM:SSZ").
// - The encoder writes no key the fixture lacks (no nulls for absent optional fields).
// - Batch (the queue's type) has `init(batchID:events:)` and `body(sourceID:)`, which
//   returns the wire JSON {schema, batch_id, source_id, events}.
import CryptoKit
import Foundation
import Testing
import TaskmineCore

private func object(_ data: Data) throws -> [String: Any] {
    guard let dict = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw CocoaError(.propertyListReadCorrupt)
    }
    return dict
}

private func json(_ dict: [String: Any]) throws -> Data {
    try JSONSerialization.data(withJSONObject: dict, options: [.sortedKeys])
}

private func sha256Hex(_ text: String) -> String {
    SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
}

/// Compares two JSON objects key by key, naming the path of the first difference.
private func expectSameJSON(_ got: [String: Any], _ want: [String: Any], path: String = "") {
    #expect(Set(got.keys) == Set(want.keys), "keys differ at \(path.isEmpty ? "root" : path)")
    for (key, wantValue) in want {
        let here = path.isEmpty ? key : "\(path).\(key)"
        guard let gotValue = got[key] else { continue }
        if let w = wantValue as? [String: Any], let g = gotValue as? [String: Any] {
            expectSameJSON(g, w, path: here)
        } else {
            let same = (gotValue as AnyObject).isEqual(wantValue)
            #expect(same, "\(here) differs")
        }
    }
}

@Suite struct WireTests {
    static let eventData = Fixtures.data("contract_fixtures/event.json")
    static let batchData = Fixtures.data("contract_fixtures/batch.json")
    static let ackData = Fixtures.data("contract_fixtures/ack.json")
    static let policyData = Fixtures.data("contract_fixtures/policy.json")

    @Test func fourContractFixtures() {
        let names = (try? FileManager.default.contentsOfDirectory(
            atPath: Fixtures.directory.appendingPathComponent("contract_fixtures").path)) ?? []
        #expect(names.sorted() == ["ack.json", "batch.json", "event.json", "policy.json"])
    }

    @Test func eventRoundTrip() throws {
        let event = try JSONDecoder().decode(Event.self, from: Self.eventData)
        let encoded = try JSONEncoder().encode(event)
        expectSameJSON(try object(encoded), try object(Self.eventData))
    }

    @Test func eventTextHashMatches() throws {
        let fixture = try object(Self.eventData)
        let content = try #require(fixture["content"] as? [String: Any])
        let text = try #require(content["text"] as? String)
        #expect(sha256Hex(text) == content["text_sha256"] as? String)

        let event = try JSONDecoder().decode(Event.self, from: Self.eventData)
        let reContent = try #require(try object(JSONEncoder().encode(event))["content"] as? [String: Any])
        let reText = try #require(reContent["text"] as? String)
        #expect(sha256Hex(reText) == reContent["text_sha256"] as? String)
        #expect(reContent["text_sha256"] as? String == content["text_sha256"] as? String)
    }

    @Test func policyRoundTrip() throws {
        let policy = try JSONDecoder().decode(Policy.self, from: Self.policyData)
        expectSameJSON(try object(JSONEncoder().encode(policy)), try object(Self.policyData))
    }

    @Test func ackRoundTrip() throws {
        let ack = try JSONDecoder().decode(Ack.self, from: Self.ackData)
        expectSameJSON(try object(JSONEncoder().encode(ack)), try object(Self.ackData))
    }

    @Test func batchRoundTrip() throws {
        let fixture = try object(Self.batchData)
        let rawEvents = try #require(fixture["events"] as? [[String: Any]])
        var events: [Data] = []
        for raw in rawEvents {
            let event = try JSONDecoder().decode(Event.self, from: try json(raw))
            events.append(try JSONEncoder().encode(event))
        }
        let batch = Batch(batchID: try #require(fixture["batch_id"] as? String), events: events)
        let body = try batch.body(sourceID: try #require(fixture["source_id"] as? String))
        let got = try object(body)
        #expect(Set(got.keys) == Set(fixture.keys))
        #expect(got["schema"] as? String == "taskmine.batch/1")
        #expect(got["batch_id"] as? String == fixture["batch_id"] as? String)
        #expect(got["source_id"] as? String == fixture["source_id"] as? String)
        let gotEvents = try #require(got["events"] as? [[String: Any]])
        #expect(gotEvents.count == rawEvents.count)
        for (g, w) in zip(gotEvents, rawEvents) { expectSameJSON(g, w) }
    }

    // MARK: rejection

    static let requiredTop = [
        "schema", "event_id", "revision", "source_id", "observed_at",
        "first_seen_at", "last_seen_at", "provenance", "content",
    ]
    static let requiredContent = ["type", "text", "text_sha256", "metadata"]

    @Test(arguments: requiredTop)
    func eventRejectsMissingField(_ field: String) throws {
        var dict = try object(Self.eventData)
        dict.removeValue(forKey: field)
        let data = try json(dict)
        #expect(throws: (any Error).self, "missing \(field) accepted") {
            _ = try JSONDecoder().decode(Event.self, from: data)
        }
    }

    @Test(arguments: requiredContent)
    func eventRejectsMissingContentField(_ field: String) throws {
        var dict = try object(Self.eventData)
        var content = try #require(dict["content"] as? [String: Any])
        content.removeValue(forKey: field)
        dict["content"] = content
        let data = try json(dict)
        #expect(throws: (any Error).self, "missing content.\(field) accepted") {
            _ = try JSONDecoder().decode(Event.self, from: data)
        }
    }

    @Test func eventRejectsMissingBundleID() throws {
        var dict = try object(Self.eventData)
        var content = try #require(dict["content"] as? [String: Any])
        var metadata = try #require(content["metadata"] as? [String: Any])
        metadata.removeValue(forKey: "app_bundle_id")
        content["metadata"] = metadata
        dict["content"] = content
        let data = try json(dict)
        #expect(throws: (any Error).self) {
            _ = try JSONDecoder().decode(Event.self, from: data)
        }
    }

    @Test func eventRejectsWrongSchema() throws {
        var dict = try object(Self.eventData)
        dict["schema"] = "taskmine.event/2"
        let data = try json(dict)
        #expect(throws: (any Error).self) {
            _ = try JSONDecoder().decode(Event.self, from: data)
        }
    }
}
