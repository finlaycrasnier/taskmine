import Foundation

/// Sends one batch per cycle to `POST {core}/v1/batches` and classifies the reply.
public actor Uploader {
    private let endpoint: URL
    private let token: @Sendable () -> String?
    private let session: URLSession

    /// An uploader to `origin`; `token` reads the bearer token for each request.
    public init(origin: URL, token: @escaping @Sendable () -> String?) {
        endpoint = origin.appendingPathComponent("v1/batches")
        self.token = token
        session = URLSession(configuration: Self.configuration(), delegate: NoRedirect(), delegateQueue: nil)
    }

    /// Ephemeral, no cookies or cache, 10 s timeout.
    static func configuration() -> URLSessionConfiguration {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 10
        config.timeoutIntervalForResource = 10
        config.httpCookieStorage = nil
        config.urlCache = nil
        config.connectionProxyDictionary = [:]
        return config
    }

    /// True for `http://127.0.0.1[:port]/...` with no user, password, query or fragment.
    static func isLoopback(_ url: URL) -> Bool {
        url.scheme == "http" && url.host == "127.0.0.1" && url.user == nil && url.password == nil
            && url.query == nil && url.fragment == nil
    }

    /// Posts the batch body and returns the ack, a retry or a stop.
    public func send(_ body: Data, batchID: String) async -> UploadResult {
        guard Self.isLoopback(endpoint) else { return .retry("not_loopback") }
        guard let token = token() else { return .retry("token_unavailable") }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.httpBody = body
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(ReaderVersion.header, forHTTPHeaderField: "X-Taskmine-Reader")
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { return .retry("invalid_reply") }
            return Self.classify(status: http.statusCode, data: data, batchID: batchID)
        } catch let error as URLError where error.code == .timedOut {
            return .retry("timeout")
        } catch {
            return .retry("offline")
        }
    }

    /// The result for an HTTP status and body.
    static func classify(status: Int, data: Data, batchID: String) -> UploadResult {
        switch status {
        case 200..<300:
            guard let ack = try? JSONDecoder().decode(Ack.self, from: data), ack.batchID == batchID else {
                return .retry("invalid_ack")
            }
            return .acked(ack)
        case 408, 425, 429, 500..<600: return .retry("server_unavailable")
        case 413: return .stop(.tooLarge)
        case 426: return .stop(.upgradeNeeded)
        case 400..<500: return .stop(.rejected)
        default: return .retry("unexpected_status")
        }
    }
}
