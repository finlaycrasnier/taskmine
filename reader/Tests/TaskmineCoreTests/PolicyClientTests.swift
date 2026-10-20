// The policy client's grace (decisions 1.11): readers stop when the core has been unreachable for 30 s.
//
// Choices the implementation must meet:
// - `current(now:)` fetches when the cache is at least `interval` (3 s) old and returns the answer.
// - When a fetch gets no answer, the last answer stays valid until `grace` (30 s) after it arrived.
// - From 30 s after the last answer, `current` returns nil: capture fails closed.
// - A later answer starts the grace again; with no answer ever, `current` returns nil.
import Foundation
import Testing
import TaskmineCore

private let t0 = Date(timeIntervalSince1970: 1_790_000_000)

private func at(_ seconds: TimeInterval) -> Date { t0.addingTimeInterval(seconds) }

private let active = Policy(version: 1, state: .active, allowedApps: ["*"], excludedDomains: [],
                            allowedProvenance: ["native_accessibility"], retentionDays: 30)

/// A stand-in core that answers while `up` is true and counts fetches.
private actor FakeCore {
    var up = true
    var fetches = 0
    func set(up: Bool) { self.up = up }
    func answer() -> Policy? {
        fetches += 1
        return up ? active : nil
    }
}

@Suite struct PolicyClientTests {
    @Test func constants() {
        #expect(PolicyClient.interval == 3)
        #expect(PolicyClient.grace == 30)
    }

    @Test func cachesForTheInterval() async {
        let core = FakeCore()
        let client = PolicyClient(fetch: { await core.answer() })
        #expect(await client.current(now: at(0)) == active)
        #expect(await client.current(now: at(2.9)) == active)
        #expect(await core.fetches == 1)
        _ = await client.current(now: at(3))
        #expect(await core.fetches == 2)
    }

    @Test func lastAnswerHoldsForTheGrace() async {
        let core = FakeCore()
        let client = PolicyClient(fetch: { await core.answer() })
        #expect(await client.current(now: at(0)) == active)
        await core.set(up: false)
        #expect(await client.current(now: at(3)) == active)
        #expect(await client.current(now: at(29.9)) == active)
        #expect(await client.current(now: at(30)) == nil)
        #expect(await client.current(now: at(60)) == nil)
    }

    @Test func answerAfterAnOutageRestartsTheGrace() async {
        let core = FakeCore()
        let client = PolicyClient(fetch: { await core.answer() })
        _ = await client.current(now: at(0))
        await core.set(up: false)
        #expect(await client.current(now: at(31)) == nil)
        await core.set(up: true)
        #expect(await client.current(now: at(34)) == active)
        await core.set(up: false)
        #expect(await client.current(now: at(63)) == active)
        #expect(await client.current(now: at(64)) == nil)
    }

    @Test func noAnswerEverFailsClosed() async {
        let core = FakeCore()
        await core.set(up: false)
        let client = PolicyClient(fetch: { await core.answer() })
        #expect(await client.current(now: at(0)) == nil)
        #expect(await client.current(now: at(3)) == nil)
    }

    @Test func invalidateFetchesAgainButKeepsTheGraceClock() async {
        let core = FakeCore()
        let client = PolicyClient(fetch: { await core.answer() })
        _ = await client.current(now: at(0))
        await core.set(up: false)
        await client.invalidate()
        #expect(await client.current(now: at(1)) == active)
        #expect(await core.fetches == 2)
        await client.invalidate()
        #expect(await client.current(now: at(30)) == nil)
    }
}
