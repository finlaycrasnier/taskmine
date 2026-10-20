// Edit burst timings (reader spec 2.7 "Edit burst", 12.7), driven by an injected clock.
//
// Choices the implementation must meet:
//   public struct BurstEvent: Equatable {
//       public enum Kind: Equatable { case started, revised, ended(EndReason) }
//       public enum EndReason: Equatable { case quietGap, maxDuration }
//       public let window: String
//       public let kind: Kind
//       public let editCount: Int        // edits seen so far (sighting_count)
//       public let durationSeconds: Int  // whole seconds from the first edit to the last edit
//   }
// - edit(window:at:) on a window without an open burst opens one and returns .started
//   (editCount 1, durationSeconds 0). Later edits only count and return nil.
// - tick() reads the clock and returns what fell due:
//   .ended(.quietGap) when now - lastEdit >= quietGap;
//   .ended(.maxDuration) when now - start >= maxDuration;
//   .revised when now - start reaches the next point of the schedule. Waits between
//   revisions are 5, 10, 20, 40, 60, 60 s, so revisions fall at 5, 15, 35, 75, 135, 195 s
//   after the start. At most 6 revisions. The closing event is extra.
// The tests tick once per second and never let two rules fall due on the same tick.
import Foundation
import Testing
import TaskmineCore

private let t0 = Date(timeIntervalSince1970: 1_790_000_000)

private final class TestClock: @unchecked Sendable {
    var seconds: Int = 0
    var now: Date { t0.addingTimeInterval(TimeInterval(seconds)) }
}

private struct Seen {
    let second: Int
    let event: BurstEvent
}

/// Runs second by second from 0 through `until`: edits due at that second first, then a tick.
private func run(
    _ burst: EditBurst, _ clock: TestClock, edits: [String: [Int]], until: Int
) -> [Seen] {
    var seen: [Seen] = []
    for s in 0...until {
        clock.seconds = s
        for window in edits.keys.sorted() where edits[window]!.contains(s) {
            if let e = burst.edit(window: window, at: clock.now) { seen.append(Seen(second: s, event: e)) }
        }
        for e in burst.tick() { seen.append(Seen(second: s, event: e)) }
    }
    return seen
}

private func every(_ step: Int, from: Int = 0, through: Int) -> [Int] {
    Array(stride(from: from, through: through, by: step))
}

@Suite struct EditBurstTests {
    @Test func firstEditStartsBurst() throws {
        let clock = TestClock()
        let burst = EditBurst(clock: { clock.now })
        let e = try #require(burst.edit(window: "w1", at: clock.now))
        #expect(e.kind == .started)
        #expect(e.window == "w1")
        #expect(e.editCount == 1)
        #expect(e.durationSeconds == 0)
        clock.seconds = 1
        #expect(burst.edit(window: "w1", at: clock.now) == nil)
    }

    @Test func noBurstNoEvents() {
        let clock = TestClock()
        let burst = EditBurst(clock: { clock.now })
        #expect(burst.tick().isEmpty)
    }

    @Test func endsAfterEightQuietSeconds() throws {
        let clock = TestClock()
        let burst = EditBurst(clock: { clock.now })
        let seen = run(burst, clock, edits: ["w1": [0, 2, 4]], until: 30)
        #expect(seen.map(\.second) == [0, 5, 12])
        #expect(seen.map(\.event.kind) == [.started, .revised, .ended(.quietGap)])
        let end = try #require(seen.last?.event)
        #expect(end.editCount == 3)
        #expect(end.durationSeconds == 4)
    }

    @Test func quietGapIsAParameter() {
        let clock = TestClock()
        let burst = EditBurst(quietGap: 3, clock: { clock.now })
        let seen = run(burst, clock, edits: ["w1": [0]], until: 10)
        #expect(seen.map(\.second) == [0, 3])
        #expect(seen.last?.event.kind == .ended(.quietGap))
    }

    @Test func endsAtMaxDuration() throws {
        let clock = TestClock()
        let burst = EditBurst(clock: { clock.now })
        // An edit every 7 s keeps the burst from going quiet.
        let seen = run(burst, clock, edits: ["w1": every(7, through: 119)], until: 125)
        #expect(seen.map(\.second) == [0, 5, 15, 35, 75, 120])
        #expect(seen.map(\.event.kind) == [.started, .revised, .revised, .revised, .revised, .ended(.maxDuration)])
        let end = try #require(seen.last?.event)
        #expect(end.editCount == 18)
        #expect(end.durationSeconds == 119)

        // The next edit in the same window opens a new burst.
        clock.seconds = 126
        let next = try #require(burst.edit(window: "w1", at: clock.now))
        #expect(next.kind == .started)
        #expect(next.editCount == 1)
    }

    @Test func maxDurationIsAParameter() {
        let clock = TestClock()
        let burst = EditBurst(maxDuration: 30, clock: { clock.now })
        let seen = run(burst, clock, edits: ["w1": every(7, through: 28)], until: 40)
        #expect(seen.map(\.second) == [0, 5, 15, 30])
        #expect(seen.last?.event.kind == .ended(.maxDuration))
    }

    @Test func revisionScheduleAndSixAtMost() {
        let clock = TestClock()
        let burst = EditBurst(maxDuration: 10_000, clock: { clock.now })
        let seen = run(burst, clock, edits: ["w1": every(7, through: 399)], until: 420)
        let revisions = seen.filter { $0.event.kind == .revised }.map(\.second)
        #expect(revisions == [5, 15, 35, 75, 135, 195])
        #expect(seen.first?.event.kind == .started)
        #expect(seen.last?.event.kind == .ended(.quietGap))
        #expect(seen.last?.second == 407)
        #expect(seen.count == 8)
    }

    @Test func revisionCarriesCountAndDuration() throws {
        let clock = TestClock()
        let burst = EditBurst(clock: { clock.now })
        let seen = run(burst, clock, edits: ["w1": every(7, through: 14)], until: 15)
        let revision = try #require(seen.first { $0.second == 15 }?.event)
        #expect(revision.kind == .revised)
        #expect(revision.editCount == 3)
        #expect(revision.durationSeconds == 14)
    }

    @Test func windowsAreIndependent() {
        let clock = TestClock()
        let burst = EditBurst(clock: { clock.now })
        let seen = run(burst, clock, edits: ["a": [0], "b": [3]], until: 20)
        let ends = seen.filter { $0.event.kind == .ended(.quietGap) }
        #expect(ends.map(\.second) == [8, 11])
        #expect(ends.map(\.event.window) == ["a", "b"])
        #expect(seen.filter { $0.event.kind == .started }.map(\.event.window) == ["a", "b"])
    }
}
