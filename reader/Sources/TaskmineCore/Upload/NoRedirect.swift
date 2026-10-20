import Foundation

/// A session delegate that refuses every redirect.
final class NoRedirect: NSObject, URLSessionTaskDelegate, Sendable {
    /// Refuses the redirect; the 3xx reply is returned as is.
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest) async -> URLRequest? {
        nil
    }
}
