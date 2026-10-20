import Foundation

/// Fetches the capture policy from the core every 3 s and caches it (docs/CONTRACTS.md section 2).
public actor PolicyClient {
    /// Seconds a fetched policy stays fresh.
    public static let interval: TimeInterval = 3
    /// Seconds the last answer stays valid after the core stops answering; then capture fails closed.
    public static let grace: TimeInterval = 30

    private let fetcher: @Sendable () async -> Policy?
    private var cached: Policy?
    private var fetchedAt: Date?
    private var answeredAt: Date?

    /// A client for `GET {origin}/v1/policy`.
    public init(origin: URL) {
        let url = origin.appendingPathComponent("v1/policy")
        let session = URLSession(configuration: Uploader.configuration(), delegate: NoRedirect(), delegateQueue: nil)
        fetcher = { await Self.fetch(url, session) }
    }

    /// A client that asks `fetch` instead of the core; nil means no answer.
    public init(fetch: @escaping @Sendable () async -> Policy?) { fetcher = fetch }

    /// The policy, fetched again when the cache is 3 s old.
    /// The last answer holds for 30 s after the core stops answering; after that, nil.
    public func current(now: Date = Date()) async -> Policy? {
        let due = fetchedAt.map { now.timeIntervalSince($0) >= Self.interval || $0 > now } ?? true
        if due {
            fetchedAt = now
            if let fresh = await fetcher() {
                cached = fresh
                answeredAt = now
            }
        }
        guard let answeredAt, answeredAt <= now, now.timeIntervalSince(answeredAt) < Self.grace else { return nil }
        return cached
    }

    /// Forgets the cache time so the next call fetches; the grace still runs from the last answer.
    public func invalidate() { fetchedAt = nil }

    /// One fetch; nil when the core cannot be reached or answers badly.
    private static func fetch(_ url: URL, _ session: URLSession) async -> Policy? {
        guard Uploader.isLoopback(url) else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(ReaderVersion.header, forHTTPHeaderField: "X-Taskmine-Reader")
        guard let (data, response) = try? await session.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return try? JSONDecoder().decode(Policy.self, from: data)
    }
}
