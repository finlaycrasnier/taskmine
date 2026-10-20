import Foundation

/// Fetches the capture policy from the core every 3 s and caches it (docs/CONTRACTS.md section 2).
public actor PolicyClient {
    /// Seconds a fetched policy stays fresh.
    public static let interval: TimeInterval = 3

    private let url: URL
    private let session: URLSession
    private var cached: Policy?
    private var fetchedAt: Date?

    /// A client for `GET {origin}/v1/policy`.
    public init(origin: URL) {
        url = origin.appendingPathComponent("v1/policy")
        session = URLSession(configuration: Uploader.configuration(), delegate: NoRedirect(), delegateQueue: nil)
    }

    /// The policy, fetched again when the cache is 3 s old; nil when the core cannot be reached or answers badly.
    public func current(now: Date = Date()) async -> Policy? {
        if let fetchedAt, now.timeIntervalSince(fetchedAt) < Self.interval, fetchedAt <= now { return cached }
        fetchedAt = now
        cached = await fetch()
        return cached
    }

    /// Forgets the cache so the next call fetches.
    public func invalidate() { fetchedAt = nil }

    /// One fetch.
    private func fetch() async -> Policy? {
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
